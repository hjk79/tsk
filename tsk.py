#!/usr/bin/env python3

import json
import argparse
import re
from pathlib import Path
from datetime import datetime, date, timedelta
import textwrap

# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

BASE = Path.home() / ".tsk"
TASK_FILE = BASE / "tasks.json"
ARCHIVE_FILE = BASE / "archive.json"

BASE.mkdir(exist_ok=True)
for f in (TASK_FILE, ARCHIVE_FILE):
    if not f.exists():
        f.write_text("[]")


def load_tasks(file):
    return json.loads(file.read_text())


def save_tasks(file, tasks):
    file.write_text(json.dumps(tasks, indent=2, ensure_ascii=False))


def next_id(tasks):
    return max((t["id"] for t in tasks), default=0) + 1


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


MAX_WIDTH = 112

ID_W   = 4
DUE_W  = 10
P_W    = 3
E_W    = 3
AGE_W  = 5

FIXED = ID_W + DUE_W + P_W + E_W + AGE_W
SEPS  = 3 * 6 + 1   # "| " and " |" for 6 columns

TITLE_W = MAX_WIDTH - FIXED - SEPS

def wrap(text, width):
    return textwrap.wrap(text, width=width) or [""]


def parse_filters(items):
    filters = []
    for item in items:
        if ":" not in item:
            raise SystemExit(f"Invalid filter '{item}', expected key:value")
        key, value = item.split(":", 1)
        filters.append((key, value))
    return filters


def task_color(t):
    today = date.today()
    due = date.fromisoformat(t["due"])

    if due < today or due == today:
        return "\033[31m"   # red
    if due == today + timedelta(days=1):
        return "\033[33m"   # yellow
    return ""


def due_state(task):
    """
    Returns: 'overdue', 'today', 'tomorrow', or None
    """
    due = date.fromisoformat(task["due"])
    today = date.today()

    if due < today:
        return "overdue"
    if due == today:
        return "today"
    if due == today + timedelta(days=1):
        return "tomorrow"
    return None

def parse_sort(spec: str):
    """
    Parse sort spec like: due,-prio
    Returns list of (key, reverse)
    """
    mapping = {
        "id": "id",
        "due": "due",
        "prio": "priority",
        "effort": "effort",
        "age": "age",
        "title": "title",
    }

    result = []
    for part in spec.split(","):
        part = part.strip()
        reverse = part.startswith("-")
        name = part[1:] if reverse else part

        if name not in mapping:
            raise SystemExit(f"Invalid sort key: {name}")

        result.append((mapping[name], reverse))

    return result


def task_age_days(task) -> int:
    created = datetime.fromisoformat(task["created"]).date()
    return (date.today() - created).days


def parse_fields(fields):
    data = {}
    for f in fields:
        if ":" not in f:
            raise ValueError(f"Invalid field '{f}', expected key:value")
        key, value = f.split(":", 1)
        data[key] = value
    return data


SPAN_RE = re.compile(r"^([+-])(\d+)([dwmy])$")


def resolve_due(value: str, base: date | None = None) -> str:
    """
    Resolve a due specification to ISO date (YYYY-MM-DD).

    value:
      - YYYY-MM-DD
      - +Nd / +Nw / +Nm / +Ny
      - -Nd / -Nw / ...

    base:
      - None  -> relative to today
      - date  -> relative to this date (e.g. existing due date)
    """

    # Absolute date
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        pass

    # Relative span
    m = SPAN_RE.match(value)
    if not m:
        raise ValueError(f"Invalid due format: {value}")

    sign, amount, unit = m.groups()
    amount = int(amount)
    if sign == "-":
        amount = -amount

    days = {
        "d": amount,
        "w": amount * 7,
        "m": amount * 30,
        "y": amount * 365,
    }[unit]

    base_date = base or date.today()
    return (base_date + timedelta(days=days)).isoformat()


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


def print_header():
    print("-" * MAX_WIDTH)
    print(
        f"| {'ID'.center(ID_W)} "
        f"| {'Due'.center(DUE_W)} "
        f"| {'P'.center(P_W)} "
        f"| {'E'.center(E_W)} "
        f"| {'Age'.center(AGE_W)} "
        f"| {'Title'.center(TITLE_W)} |"
    )
    print("-" * MAX_WIDTH)


RESET = "\033[0m"

def print_task_row(t):
    title_lines = wrap(t["title"], TITLE_W)
    color = t.get("color", "")

    for line in title_lines:
        print(
            color +
            f"| {str(t['id']).center(ID_W)} "
            f"| {t['due'].center(DUE_W)} "
            f"| {str(t['priority']).center(P_W)} "
            f"| {str(t['effort']).center(E_W)} "
            f"| {t['age'].center(AGE_W)} "
            f"| {line.ljust(TITLE_W)} |"
            + RESET
        )


def build_filter_fn(filters):
    def match(task):
        for key, value in filters:
            if key == "prio":
                if task["priority"] != int(value):
                    return False

            elif key == "effort":
                if task["effort"] != int(value):
                    return False

            elif key == "status":
                if task["status"] != value:
                    return False

            elif key == "title":
                if value.lower() not in task["title"].lower():
                    return False

            elif key == "comment":
                if value.lower() not in task["comment"].lower():
                    return False

            elif key == "due":
                today = date.today()
                due = date.fromisoformat(task["due"])

                if value == "today" and due != today:
                    return False
                elif value == "tomorrow" and due != today + timedelta(days=1):
                    return False
                elif value.startswith("<"):
                    limit = date.fromisoformat(
                        resolve_due(value[1:])
                    )
                    if not due < limit:
                        return False
                elif value.startswith(">"):
                    limit = date.fromisoformat(
                        resolve_due(value[1:])
                    )
                    if not due > limit:
                        return False
                else:
                    if due != date.fromisoformat(resolve_due(value)):
                        return False

            else:
                raise SystemExit(f"Unknown filter key: {key}")

        return True

    return match


def add_task(args):
    tasks = load_tasks(TASK_FILE)
    fields = parse_fields(args.fields)

    # print("DEBUG fields:", fields)

    if "due" not in fields:
        raise SystemExit("Missing required field: due")

    try:
        due = resolve_due(fields["due"])
    except ValueError as e:
        raise SystemExit(str(e))

    task = {
        "id": next_id(tasks),
        "created": datetime.now().isoformat(timespec="seconds"),
        "due": due,
        "title": args.title,
        "comment": fields.get("comment", ""),
        "priority": int(fields.get("prio", 3)),
        "effort": int(fields.get("effort", 1)),
        "status": "open",
    }

    tasks.append(task)
    save_tasks(TASK_FILE, tasks)
    print(f"Added task {task['id']} (due {due})")


def list_tasks(filter_fn=None, sort_spec=None):
    tasks = load_tasks(TASK_FILE)

    # 1️⃣ filter
    if filter_fn:
        tasks = [t for t in tasks if filter_fn(t)]

    # 2️⃣ enrich tasks (age, coloring, etc.)
    now = datetime.now()
    for t in tasks:
        t["color"] = task_color(t)
        created = datetime.fromisoformat(t["created"])
        t["age"] = f"{(now - created).days}d"

    # 3️⃣ sort
    if sort_spec:
        tasks = sort_tasks(tasks, sort_spec)

    # 4️⃣ output
    if not tasks:
        print("No tasks.")
        return

    print_header()
    for t in tasks:
        print_task_row(t)
    print("-" * MAX_WIDTH)


def sort_tasks(tasks, sort_spec=None):
    if not sort_spec:
        # default: due, then priority
        sort_spec = [("due", False), ("priority", False)]

    for key, reverse in reversed(sort_spec):
        if key == "age":
            tasks.sort(
                key=lambda t: task_age_days(t),
                reverse=reverse,
            )
        else:
            tasks.sort(
                key=lambda t: t[key],
                reverse=reverse,
            )


def close_task(task_id):
    tasks = load_tasks(TASK_FILE)
    for t in tasks:
        if t["id"] == task_id:
            t["status"] = "done"
            save_tasks(TASK_FILE, tasks)
            print(f"Closed task {task_id}")
            return
    print("Task not found")


def delete_task(task_id):
    tasks = load_tasks(TASK_FILE)
    archive = load_tasks(ARCHIVE_FILE)

    for t in tasks:
        if t["id"] == task_id:
            tasks.remove(t)
            archive.append(t)
            save_tasks(TASK_FILE, tasks)
            save_tasks(ARCHIVE_FILE, archive)
            print(f"Archived task {task_id}")
            return
    print("Task not found")


def postpone(task_id, value):
    tasks = load_tasks(TASK_FILE)

    for t in tasks:
        if t["id"] == task_id:
            try:
                base = date.fromisoformat(t["due"])
                new_due = resolve_due(value, base=base)
            except ValueError as e:
                raise SystemExit(str(e))

            t["due"] = new_due
            save_tasks(TASK_FILE, tasks)
            print(f"Postponed task {task_id} >>> {new_due}")
            return

    print("Task not found")


def due_today():
    today = date.today().isoformat()
    list_tasks(lambda t: t["due"] == today and t["status"] == "open")

def due_today(sort_spec=None, extra_filters=None):
    today = date.today().isoformat()

    def base(t):
        return t["due"] == today and t["status"] == "open"

    fn = base
    if extra_filters:
        extra = build_filter_fn(extra_filters)
        fn = lambda t: base(t) and extra(t)

    list_tasks(fn, sort_spec)

def due_week(sort_spec=None, extra_filters=None):
    start = date.today()
    end = start + timedelta(days=7)

    def base(t):
        d = date.fromisoformat(t["due"])
        return start <= d <= end and t["status"] == "open"

    fn = base

    if extra_filters:
        extra = build_filter_fn(extra_filters)
        fn = lambda t: base(t) and extra(t)

    list_tasks(filter_fn=fn, sort_spec=sort_spec)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

parser = argparse.ArgumentParser(
    prog="tsk",
    description="A minimal task and todo manager inspired by taskwarrior.",
    epilog="""
DATE FORMATS
  Absolute:   YYYY-MM-DD
  Relative:   +Nd, +Nw, +Nm, +Ny
  Examples:   due:2025-01-10
              due:+7d
              postpone 3 +2w

FILTERS (key:value, AND-combined)
  prio:N            priority equals N
  effort:N          effort equals N
  status:open|done
  title:TEXT        substring match
  comment:TEXT      substring match
  due:DATE          exact date
  due:<DATE         before date
  due:>DATE         after date
  due:today
  due:tomorrow

SORTING
  --sort KEY[,KEY]
  Prefix with '-' for descending.

  Keys: id, due, prio, effort, age, title
  Examples:
    --sort due
    --sort due,-prio

EXAMPLES
  tsk add "Write report" due:+7d prio:1
  tsk list prio:1 --sort due
  tsk today
  tsk week prio:1
  tsk postpone 3 +2d
""",
    formatter_class=argparse.RawDescriptionHelpFormatter,
)

sub = parser.add_subparsers(dest="cmd")

a = sub.add_parser(
    "add",
    help="Add a new task",
    description="""
Add a new task.

FIELDS (key:value)
  due:DATE        (required)
  prio:N          priority (default: 3)
  effort:N        effort estimate (default: 1)
  comment:TEXT

DATE can be:
  YYYY-MM-DD
  +Nd, +Nw, +Nm, +Ny

Examples:
  tsk add "Write report" due:+7d prio:1
  tsk add "Pay taxes" due:2025-01-31 comment:"important"
""",
    formatter_class=argparse.RawDescriptionHelpFormatter,
)
a.add_argument("title", help="Task title")
a.add_argument("fields", nargs="*", help="key:value fields")

l = sub.add_parser(
    "list",
    help="List tasks",
    description="""
List tasks with optional filters and sorting.

FILTERS
  key:value pairs (AND-combined)

SORTING
  --sort KEY[,KEY]

Examples:
  tsk list
  tsk list prio:1
  tsk list status:open --sort due
""",
    formatter_class=argparse.RawDescriptionHelpFormatter,
)
l.add_argument("filters", nargs="*", help="filter expressions")
l.add_argument("--sort", help="sort keys, e.g. due,-prio")

t = sub.add_parser(
    "today",
    help="List tasks due today",
    description="""
List open tasks due today.

Supports the same filters and sorting as 'list'.

Examples:
  tsk today
  tsk today prio:1
""",
    formatter_class=argparse.RawDescriptionHelpFormatter,
)
t.add_argument("filters", nargs="*", help="filter expressions")
t.add_argument("--sort", help="sort keys")

w = sub.add_parser(
    "week",
    help="List tasks due within the next 7 days",
    description="""
List open tasks due within the next 7 days.

Supports filters and sorting.

Examples:
  tsk week
  tsk week prio:1 --sort due
""",
    formatter_class=argparse.RawDescriptionHelpFormatter,
)
w.add_argument("filters", nargs="*", help="filter expressions")
w.add_argument("--sort", help="sort keys")

c = sub.add_parser("close")
c.add_argument("id", type=int)

d = sub.add_parser("delete")
d.add_argument("id", type=int)

p = sub.add_parser(
    "postpone",
    help="Postpone or reschedule a task",
    description="""
Change a task's due date.

VALUE can be:
  +Nd / -Nd
  +Nw / -Nw
  YYYY-MM-DD

Examples:
  tsk postpone 3 +2d
  tsk postpone 3 2025-02-01
""",
    formatter_class=argparse.RawDescriptionHelpFormatter,
)
p.add_argument("id", type=int, help="task id")
p.add_argument("value", help="date or span")

args = parser.parse_args()

if args.cmd == "add":
    add_task(args)

elif args.cmd == "list":
    filters = parse_filters(args.filters)
    fn = build_filter_fn(filters)
    spec = parse_sort(args.sort) if args.sort else None
    list_tasks(filter_fn=fn, sort_spec=spec)

elif args.cmd == "today":
    filters = parse_filters(args.filters)
    spec = parse_sort(args.sort) if args.sort else None
    due_today(spec, filters)

elif args.cmd == "week":
    filters = parse_filters(args.filters)
    spec = parse_sort(args.sort) if args.sort else None
    due_week(sort_spec=spec, extra_filters=filters)

elif args.cmd == "close":
    close_task(args.id)

elif args.cmd == "delete":
    delete_task(args.id)

elif args.cmd == "postpone":
    postpone(args.id, args.value)

else:
    parser.print_help()
