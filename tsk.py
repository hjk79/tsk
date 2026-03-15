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
    archive = load_tasks(ARCHIVE_FILE)

    all_ids = [t["id"] for t in tasks] + [t["id"] for t in archive]

    return max(all_ids, default=0) + 1


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TIME_FMT = "%Y-%m-%d %H:%M"

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

def parse_sort_spec(sort_str: str):
    """
    Convert --sort string into list of (key, reverse) tuples.
    Example:
        "due" -> [("due", False)]
        "-priority" -> [("priority", True)]
        "due,-priority" -> [("due", False), ("priority", True)]
    """
    if not sort_str:
        return None

    specs = []
    for part in sort_str.split(","):
        part = part.strip()
        if not part:
            continue
        if part.startswith("-"):
            specs.append((part[1:], True))
        else:
            specs.append((part, False))
    return specs

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

def add_comment(task_id, text):
    """Append a comment to a task (active or archived)."""

    # determine where the task lives
    source = TASK_FILE
    tasks = load_tasks(source)

    task = next((t for t in tasks if t["id"] == task_id), None)

    if not task:
        source = ARCHIVE_FILE
        tasks = load_tasks(source)
        task = next((t for t in tasks if t["id"] == task_id), None)

    if not task:
        print(f"Task {task_id} not found.")
        return

    # convert legacy comment if needed
    if "comments" not in task:
        task["comments"] = []
        if task.get("comment"):
            task["comments"].append({
                "time": task["created"],
                "text": task["comment"]
            })
            del task["comment"]

    # append new comment
    task["comments"].append({
        "time": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "text": text
    })

    save_tasks(source, tasks)

    print(f"Added comment to task {task_id}.")

def show_task(task_id):
    """Display full details of a single task."""
    tasks = load_tasks(TASK_FILE)
    task = next((t for t in tasks if t["id"] == task_id), None)

    source = "active"

    if not task:
        tasks = load_tasks(ARCHIVE_FILE)
        task = next((t for t in tasks if t["id"] == task_id), None)
        source = "archive"

    if not task:
        print(f"Task {task_id} not found.")
        return

    now = datetime.now()
    age = (now - datetime.fromisoformat(task["created"])).days

    print("-" * MAX_WIDTH)

    print(f"ID      : {task['id']} ({source})")
    print(f"Title   : {task['title']}")
    print(f"Created : {task['created']}")
    print(f"Due     : {task['due']}")
    print(f"Priority: {task['priority']}")
    print(f"Effort  : {task['effort']}")
    print(f"Age     : {age}d")

    # --- comments section ---
    if "comments" in task and task["comments"]:
        print("\nComments")
        print("-" * MAX_WIDTH)

        TS_W = 16  # width of timestamp column
        TEXT_W = MAX_WIDTH - TS_W - 2

        for c in task["comments"]:
            ts = c.get("time", "")
            text = c.get("text", "")

            lines = wrap(text, TEXT_W)

            for i, line in enumerate(lines):
                if i == 0:
                    print(f"{ts.ljust(TS_W)}  {line}")
                else:
                    print(f"{' '*TS_W}  {line}")

    elif task.get("comment"):
        print("\nComment")
        print("-" * MAX_WIDTH)

        for line in wrap(task["comment"], MAX_WIDTH):
            print(line)

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

    for i, line in enumerate(title_lines):
        if i == 0:
            # first line: print all columns
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
        else:
            # subsequent lines: leave other columns blank
            print(
                color +
                f"| {'':{ID_W}} "
                f"| {'':{DUE_W}} "
                f"| {'':{P_W}} "
                f"| {'':{E_W}} "
                f"| {'':{AGE_W}} "
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
    }

    tasks.append(task)
    save_tasks(TASK_FILE, tasks)
    print(f"Added task {task['id']} (due {due})")


def list_tasks(filter_fn=None, sort_spec=None, archive=False):
    source = ARCHIVE_FILE if archive else TASK_FILE
    tasks = load_tasks(source)

    # 1 filter
    if filter_fn:
        tasks = [t for t in tasks if filter_fn(t)]

    # 2 enrich tasks (age, coloring, etc.)
    now = datetime.now()
    for t in tasks:
        created = datetime.fromisoformat(t["created"])
        t["age"] = f"{(now - created).days}d"
       
        if archive:
            t["color"] = ""
        else:
            t["color"] = task_color(t)

    # 3 sort
    if sort_spec:
        # If sort_spec is a string from CLI, parse it into list of tuples
        if isinstance(sort_spec, str):
            sort_spec = parse_sort_spec(sort_spec)
        # sort in place
        sort_tasks(tasks, sort_spec)

    # 4 output
    if not tasks:
        print("No tasks.")
        return

    print_header()
    for t in tasks:
        print_task_row(t)
    print("-" * MAX_WIDTH)


def sort_tasks(tasks, sort_spec=None):
    if not sort_spec:
        sort_spec = [("due", False), ("priority", False)]

    for key, reverse in reversed(sort_spec):
        if key == "age":
            tasks.sort(
                key=lambda t: (datetime.now() - datetime.fromisoformat(t["created"])).days,
                reverse=reverse,
            )
        elif key == "due":
            tasks.sort(
                key=lambda t: date.fromisoformat(t["due"]),
                reverse=reverse,
            )
        elif key in ("priority", "effort", "id"):
            tasks.sort(
                key=lambda t: int(t[key]),  # numeric sort
                reverse=reverse,
            )
        else:
            # string fields
            tasks.sort(
                key=lambda t: t[key].lower(),
                reverse=reverse,
            )


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
    list_tasks(lambda t: t["due"] == today)

def due_today(sort_spec=None, extra_filters=None):
    today = date.today().isoformat()

    def base(t):
        return t["due"] == today

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
        return start <= d <= end

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

s = sub.add_parser("show", help="Show detailed task by ID")
s.add_argument("id", type=int, help="ID of the task to show")
s.add_argument("-a", "--archive", action="store_true", help="show task from archive")

c = sub.add_parser("comment", help="Add comment to a task")
c.add_argument("id", type=int, help="Task ID")
c.add_argument("text", help="Comment text")

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
""",
    formatter_class=argparse.RawDescriptionHelpFormatter,
)
l.add_argument("filters", nargs="*", help="filter expressions")
l.add_argument("--sort", help="sort keys, e.g. due,-prio")
l.add_argument("-a", "--archive", action="store_true", help="show archived tasks")

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

elif args.cmd == "comment":
    add_comment(args.id, args.text)

elif args.cmd == "show":
    show_task(args.id)

elif args.cmd == "list":
    filters = parse_filters(args.filters)
    fn = build_filter_fn(filters)
    spec = parse_sort(args.sort) if args.sort else None
    list_tasks(filter_fn=fn, sort_spec=spec, archive=args.archive)

elif args.cmd == "today":
    filters = parse_filters(args.filters)
    spec = parse_sort(args.sort) if args.sort else None
    due_today(spec, filters)

elif args.cmd == "week":
    filters = parse_filters(args.filters)
    spec = parse_sort(args.sort) if args.sort else None
    due_week(sort_spec=spec, extra_filters=filters)

elif args.cmd == "delete":
    delete_task(args.id)

elif args.cmd == "postpone":
    postpone(args.id, args.value)

else:
    parser.print_help()
