# Clean Code in Python — A Learning Guide

> A comprehensive but concise walkthrough of *Clean Code in Python*, 2nd ed. (Mariano Anaya, Packt 2021), synthesized from a full read of all ten chapters. Read this to **learn** the ideas; when you sit down to **refactor**, use the companion [refactoring checklist](clean-code-python-refactoring-checklist.md).
>
> **How to read this:** each chapter section gives you the *problem*, the *principle*, the *Python mechanics*, a *worked before→after example*, and the *pitfalls*. The code snippets are the point — read them, don't skim them.

## The one idea behind the whole book

**Code is communication between developers, not just instructions for a machine.** You read code far more than you write it. So "clean" means whatever makes the code *maintainable*: readable, changeable, and low in technical debt. Three consequences run through every chapter:

1. **Formatting and linting are the floor, not the goal.** A file can be 100% PEP-8 compliant and still be terrible. Automate the floor so humans review *meaning*.
2. **Technical debt is interest you pay later.** Every shortcut makes tomorrow's change more expensive. Unchangeable software is useless software, because requirements always change.
3. **These are principles, not laws.** The book's closing maxim, echoing the Zen of Python: ***practicality beats purity.*** The real goal is critical thinking, not recipes.

## Table of Contents
1. [Introduction, Code Formatting, and Tools](#1--introduction-code-formatting-and-tools)
2. [Pythonic Code](#2--pythonic-code)
3. [General Traits of Good Code](#3--general-traits-of-good-code)
4. [The SOLID Principles](#4--the-solid-principles)
5. [Using Decorators to Improve Our Code](#5--using-decorators-to-improve-our-code)
6. [Getting More Out of Our Objects with Descriptors](#6--getting-more-out-of-our-objects-with-descriptors)
7. [Generators, Iterators, and Asynchronous Programming](#7--generators-iterators-and-asynchronous-programming)
8. [Unit Testing and Refactoring](#8--unit-testing-and-refactoring)
9. [Common Design Patterns](#9--common-design-patterns)
10. [Clean Architecture](#10--clean-architecture)
- [Cheat Sheet](#cheat-sheet)

---

## 1 — Introduction, Code Formatting, and Tools

**The problem.** Why care about "clean" at all? Anaya's analogy: delivering features is like driving to a destination on a deadline. On a smooth road you can estimate arrival; on a cracked road full of rocks you must keep stopping to clear the way, and your estimate becomes worthless. **The road is your code.** A maintainable codebase lets a team deliver at a steady, predictable pace; a debt-ridden one forces constant detours to refactor before every new feature. The opposite of *agile* is *rigid* — and code riddled with smells is rigid.

**Clean code is not formatting.** No tool can score how clean code is; cleanliness is judged by another engineer reading it. Consistency is what makes that reading fast. The book cites *Perceptions in Chess*: shown a realistic board, masters vastly outperform novices at recall; shown a *random* board, masters do no better than novices. Familiar structure is what lets an expert spot what's wrong at a glance. Random structure demotes your experts to beginners.

**Documentation vs. comments.** Aim for *few* comments — a comment explaining *what* the code does is "a symptom of our inability to express the code correctly," and it rots as the code changes. Delete commented-out code mercilessly; version control remembers it. What you *should* invest in:

- **Docstrings** = documentation embedded in code (the `__doc__` attribute). They explain *what* a component does and *how to use it* — ideally with example inputs/outputs, which double as test fixtures.
- **Annotations** (PEP-3107/484) = type hints that help readers *and* tools. They also let you name domain concepts:

```python
# A bare type tells the reader nothing about the concept:
def launch_task(delay: float): ...

# A named alias is a one-place abstraction with meaning:
Seconds = float
def launch_task(delay: Seconds): ...
```

Annotations and docstrings *complement* each other — annotations carry the types, docstrings carry intent and concrete examples.

**Automate the floor.** Wire up the tools and make CI fail on violation, so "you broke PEP-8" stops being a recurring code-review argument and becomes objective:

| Tool | Job |
|---|---|
| `black` | deterministic auto-formatting (no bikeshedding); `yapf` for partial/legacy reformatting |
| `mypy` / `pytype` | static type-consistency checks from your annotations |
| `pylint` / `flake8` / `pycodestyle` | style + structural lint (e.g. "too many arguments") |

Tie them together in a `Makefile` so every dev and CI runs the same thing:

```makefile
checklist: lint typehint test   # one command, runs everything; any failure fails the build
```

**Legitimate exceptions.** Not everything needs polish: hackathons, one-off scripts, code competitions, proofs of concept, throwaway prototypes, soon-to-be-deprecated legacy. The common thread — *code you'll genuinely never read again doesn't need to be maintainable.*

**Takeaway:** Make quality automatic and objective at the floor level, so human attention goes to what only humans can judge — whether the code communicates its intent.

---

## 2 — Pythonic Code

**The problem.** Every language has *idioms* — the conventional way to do a task. Code that follows them is "idiomatic," or in Python, "Pythonic." Idiomatic code is more compact, more readable, and usually faster, because it cooperates with Python's own protocols (the "magic" / dunder methods) instead of fighting them. Coming from C/C++/Java, you'll reach for non-Pythonic patterns by reflex; this chapter retrains those reflexes.

### Indexing and slicing
Use negative indices and slices instead of arithmetic and manual loops. Slices accept `start:stop:step` (stop excluded) and are really a `slice()` object under the hood:

```python
xs[-1]        # last element (not xs[len(xs)-1])
xs[1:-1]      # all but first and last
xs[::2]       # every other element
```

### Magic methods make your objects behave like built-ins
This is the core of being Pythonic — implement the protocol and the language's syntax just works on your object:

| You want… | Implement… |
|---|---|
| `obj[key]`, slicing | `__getitem__` (+ `__len__` for a sequence) |
| `for x in obj` | `__iter__` / `__next__` |
| `x in obj` | `__contains__` |
| `obj.attr` resolution fallback | `__getattr__` |
| `obj(...)` callable | `__call__` |
| `with obj:` | `__enter__` / `__exit__` |

A concrete win — replacing a duplicated boundary check with `in`:

```python
# Before: condition logic repeated everywhere
def mark(grid, coord):
    if 0 <= coord.x < grid.width and 0 <= coord.y < grid.height:
        grid[coord] = MARKED

# After: the grid knows what "contains" means
class Grid:
    def __contains__(self, coord):
        return 0 <= coord.x < self.width and 0 <= coord.y < self.height

def mark(grid, coord):
    if coord in grid:
        grid[coord] = MARKED
```

### Context managers — not just for resources
A `with` block guarantees `__exit__` runs even on exception. Beyond file/connection cleanup, use them to bracket *any* before/after logic (a separation-of-concerns tool). Three ways to build one:

```python
# 1. A class with __enter__/__exit__
# 2. A generator (cleanest when it's not tied to an object):
import contextlib
@contextlib.contextmanager
def db_offline():
    stop_database()
    try:
        yield            # everything before yield = __enter__, after = __exit__
    finally:
        start_database()

# 3. contextlib.suppress to document an intentionally-ignored exception:
with contextlib.suppress(FileNotFoundError):
    os.remove(path)
```

### Comprehensions and the walrus
Build collections declaratively, not with an empty list + `append`. Use the walrus `:=` (3.8+) to bind a value inside an expression so you don't compute it twice:

```python
# Before
result = []
for line in lines:
    m = re.match(PATTERN, line)
    if m: result.append(m.group("id"))

# After
result = [m.group("id") for line in lines if (m := re.match(PATTERN, line))]
```

### Properties and attributes
Python attributes are already public — don't write Java-style `get_x`/`set_x`. Use a plain attribute by default; reach for `@property` only when access needs logic or validation. This also enables command/query separation (a getter answers; a setter acts):

```python
@property
def latitude(self) -> float:
    return self._latitude

@latitude.setter
def latitude(self, value: float) -> None:
    if value not in range(-90, 91):
        raise ValueError(f"{value} is invalid for latitude")
    self._latitude = value
```

### Underscores — convention vs. mangling
- `_x` (single) = the convention for "internal, not part of the public interface." This is what you want 99% of the time.
- `__x` (double) is **not privacy** — it triggers *name mangling* to `_ClassName__x`, a mechanism to avoid attribute collisions in subclasses. Using it for "privacy" just confuses readers and breaks access.

### Dataclasses
`@dataclass` removes `self.x = x` boilerplate. Critically, it gives you the *right* way to handle mutable defaults:

```python
from dataclasses import dataclass, field
@dataclass
class Node:
    value: int
    children: list = field(default_factory=list)   # fresh list per instance
    def __post_init__(self):                        # validation / derived data
        ...
```

### Two foot-guns to memorize
**Mutable default arguments** are evaluated *once*, at definition time, and shared across all calls:

```python
def add(item, items=[]):      # BUG: the same list persists between calls
    items.append(item); return items

def add(item, items=None):    # FIX: sentinel
    items = items if items is not None else []
    items.append(item); return items
```

**Don't subclass built-in collections** (`dict`/`list`/`str`) — in CPython their methods don't call your overrides, so behavior is inconsistent. Use `collections.UserDict`/`UserList`/`UserString`.

**Takeaway:** Learn the protocols. Pythonic code isn't about cleverness — it's about letting the language's own machinery express your intent.

---

## 3 — General Traits of Good Code

**The big idea:** the code *is* the design — it's the most detailed expression of it. So "good code" means *robust* code: it either minimizes defects, or makes them loud and easy to localize. This chapter is a toolbox of language-agnostic principles applied Pythonically.

### Design by Contract (DbC)
Treat the boundary between caller and function as a contract with three parts:
- **Preconditions** — the *caller's* duty: valid inputs/state. The function should validate them and fail loudly if violated (a "demanding" stance). A precondition failure means the *client* is buggy.
- **Postconditions** — the *function's* duty: guarantee the promised result. A failure here means the *function* is buggy.
- **Invariants** (documented in the docstring) — things held true throughout.

Each check is owned by exactly one party (the *non-redundancy* principle — a sibling of DRY). Make contracts *meaningful*; don't just check types (that's mypy's job).

### Defensive programming
Two distinct tools for two distinct situations:
- **Error handling** for errors you *expect* (bad input, network blips).
- **Assertions** for situations that *should be impossible* (a defect if they happen).

**Exception handling — the rules that matter most:**

```python
# NEVER do this — the "most diabolical anti-pattern": errors pass silently.
try:
    process()
except:
    pass

# Catch the specific exception, and when re-raising a different one, chain the cause:
try:
    return data[record_id]
except KeyError as e:
    raise InternalDataError("Record not present") from e   # preserves traceback
```

Also: don't use exceptions as business control flow; handle each exception at the abstraction level it belongs to (don't mix a low-level `ConnectionError` and a domain `ValueError` in one handler); never leak tracebacks to end users (log internally, show a generic message).

**Assertions** check impossible conditions — never use them for business logic, never put a side-effecting call inside one, and never run production with `python -O` (it strips them):

```python
result = condition.holds()       # evaluate once into a local
assert result, f"unexpected: {result}"
```

### Cohesion, coupling, and the acronyms
The headline rule: **aim for high cohesion and low coupling.** Cohesive objects do one small thing well (and are reusable); coupled objects depend on each other (causing ripple effects). The supporting acronyms:

- **DRY / OAOO** — each piece of *knowledge* lives in exactly one place. Duplication isn't about identical text; it's about a fact that can drift. Name it once:
  ```python
  def score_for_student(s):
      return s.passed * 11 - s.failed * 5 - s.years * 2
  sorted(students, key=score_for_student)   # not the formula inlined in two places
  ```
- **YAGNI** — build for *current* requirements, not imagined futures. Speculative abstractions are usually the wrong ones and are harder to undo than no abstraction.
- **KIS** — keep it simple; pick the smallest data structure / least machinery that fits. A little duplication can beat a complicated abstraction.
- **EAFP > LBYL** — prefer "ask forgiveness" (`try/except`) over "look before you leap" (`if exists(...)`). It's more direct and intention-revealing in Python, with no performance penalty.

### Inheritance — use it sparingly
A subclass is *tightly coupled* to its parent, and the parent's methods become part of the subclass's public interface. So **never inherit just for code reuse.** Good uses: genuine specialization, defining an interface (ABCs), and exception hierarchies. The classic anti-pattern is extending a data structure to reuse it:

```python
# Anti-pattern: a Policy is NOT a dict; it now exposes pop(), items(), ...
class TransactionalPolicy(collections.UserDict): ...

# Fix with composition: hold the dict privately, expose only what you mean to
class TransactionalPolicy:
    def __init__(self, data): self._data = dict(data)
    def __getitem__(self, k): return self._data[k]
    def change_in_policy(self, cid, **kw): self._data[cid].update(**kw)
```

(Also covered: multiple inheritance, the **MRO** via C3 linearization — inspect with `Cls.mro()` — and **mixins** for reusable behavior combined via multiple inheritance.)

### Function arguments
Arguments are passed by value-of-reference: rebinding a parameter doesn't affect the caller, but mutating a mutable one does — so **don't mutate your arguments.** Don't fish keys out of `**kwargs`; declare them. Prefer **keyword-only** args (after `*`) for clarity and backward-compatible extension. And watch the **count** — too many parameters is a smell (tight coupling, a leaky abstraction). Fix by *reifying*:

```python
track_request(request.headers, request.ip_addr, request.request_id)  # smell
track_request(request)                                               # pass the object
```

Reserve bare `*args, **kwargs` for true wrappers (decorators, `super()`), where the signature is meant to be transparent.

**Takeaway:** Robustness comes from clear contracts, loud failures, one-fact-one-place, and composition over inheritance — each keeps the blast radius of a change or a bug small.

---

## 4 — The SOLID Principles

**The big idea:** don't try to nail the design on the first attempt. Instead, **design toward abstractions so that new requirements mean adding code, not modifying existing code.** Python's duck typing, `abc` module, mypy/pylint, and constructor injection make all five principles practical. The principles reinforce each other (LSP and DIP both enable OCP).

### S — Single Responsibility
A class should have *one reason to change*. Split god-classes whose methods form unrelated clusters — e.g. a `SystemMonitor` that loads data *and* parses events *and* streams them becomes three collaborators. (This is *not* "one method per class" — methods serving the same responsibility belong together.)

### O — Open/Closed
Open to extension, closed to modification. The telltale violation is an `if/elif` chain that grows every time a new case appears. Replace it with polymorphism:

```python
# Closed method, open hierarchy — adding an event means adding a class, not editing this:
class SystemMonitor:
    def identify_event(self):
        for event_cls in Event.__subclasses__():
            if event_cls.meets_condition(self.event_data):
                return event_cls(self.event_data)
        return UnknownEvent(self.event_data)

class LoginEvent(Event):
    @staticmethod
    def meets_condition(data):
        return data["before"].get("session") == 0 and data["after"].get("session") == 1
```

The success test: a new feature touches only *new* code.

### L — Liskov Substitution
Any subtype must be a drop-in replacement for its base. A subclass must **not** strengthen a precondition (demand more than the base), weaken a postcondition (promise less), or change a method's signature/return type. Tooling catches much of this — mypy flags incompatible types, pylint flags `arguments-differ`. When it does, **fix the design; never silence it with `# type: ignore`** — the tool is reporting a real flaw. LSP is what makes OCP safe.

### I — Interface Segregation
Prefer many small, cohesive interfaces over one fat one. An interface bundling `from_xml` + `from_json` forces a JSON-only implementer to write a useless `from_xml`. Split into separate ABCs and recombine via multiple inheritance only where a class truly needs both. Don't over-segregate, though — methods that must operate together (`__enter__`/`__exit__`) stay together. "Small" means *cohesive*, not "one method."

### D — Dependency Inversion
High-level code should depend on **abstractions, not concrete details**, and the volatile detail should adapt to *your* interface. The practical mechanism is **dependency injection** through `__init__`:

```python
# Before: high-level class hard-wires a low-level detail — rigid, hard to test
class EventStreamer:
    def __init__(self):
        self._target = Syslog()

# After: depends on an abstraction, receives the concrete one — swap/test freely
class EventStreamer:
    def __init__(self, target: DataTargetClient):
        self._target = target
```

The rule, in the book's words: *"Don't force the creation of dependencies in the initialization method."*

**Takeaway:** SOLID is one idea wearing five hats — depend on stable abstractions so the system grows by addition. New requirement → new class, not a scar across old code.

---

## 5 — Using Decorators to Improve Our Code

**The mechanics.** `@decorator` over `def f` is exactly `f = decorator(f)` — a reusable, black-box transformation applied to a function, method, class, generator, or coroutine. They're a powerful tool for DRY and separation of concerns, but each adds a layer of indirection, so they pay off only with *real* reuse.

**The rule you must never forget — `functools.wraps`.** Without it the wrapper replaces the original's `__name__`, `__doc__`, `__annotations__`, and doctests — every decorated function reports itself as `wrapped`, breaking `help()`, logging, and debugging:

```python
def trace(function):
    @functools.wraps(function)         # <-- preserves the original's identity
    def wrapped(*args, **kwargs):
        logger.info("running %s", function.__qualname__)
        return function(*args, **kwargs)
    return wrapped
```

**Parameterized decorators — prefer a class.** You can do it with three nested functions, but a class (params in `__init__`, logic in `__call__`) is more readable and can hold state:

```python
class WithRetry:
    def __init__(self, retries=3):
        self.retries = retries
    def __call__(self, operation):
        @functools.wraps(operation)
        def wrapped(*a, **kw):
            for _ in range(self.retries):
                try:
                    return operation(*a, **kw)
                except ControlledException as e:
                    last = e
            raise last
        return wrapped

@WithRetry(retries=5)
def run(): ...
```

**The classic bug — import-time side effects.** Code placed in the decorator body (outside `wrapped`) runs *once, at import/decoration time*, not per call. A timer started there measures from import, not from invocation. Move side effects *inside* `wrapped`. (The exception: deliberate registration, e.g. a `register_event` decorator populating a module-level registry, belongs in the outer scope on purpose.)

**Make it work on methods too.** A plain-function decorator breaks on a method because of the extra `self`. Fix with generic `*args, **kwargs`, or by implementing the decorator as a class with `__get__` (the descriptor protocol — see Ch. 6).

**When *not* to use one.** Apply the **rule of three**: don't extract a decorator until the pattern has appeared ≥3 times. Keep each to a single responsibility (split "log" and "time" into two decorators and stack them — order matters). For little reuse, a plain function is clearer. Done well, decorators are an excellent way to define a clean public API (`@app.task`, `@route`).

**Takeaway:** Decorators trade indirection for reuse. Earn that trade (rule of three), preserve identity (`wraps`), and keep side effects at call time.

---

## 6 — Getting More Out of Our Objects with Descriptors

**What they are.** A descriptor is a class implementing any of `__get__`, `__set__`, `__delete__`, `__set_name__`, placed as a **class attribute**. It intercepts attribute access, letting you factor repeated access logic (validation, transformation, tracing) into one reusable object. Descriptors are how Python itself implements methods, `@property`, `@classmethod`, `@staticmethod`, and `__slots__` — you've been using them all along.

**The headline use — kill repetitive `@property` boilerplate.** If five fields all need the same validation, write one descriptor instead of five property pairs:

```python
class Field:
    def __set_name__(self, owner, name):     # learns its own attribute name (3.6+)
        self._name = name
    def __get__(self, instance, owner):
        if instance is None:
            return self
        return instance.__dict__[self._name]
    def __set__(self, instance, value):
        for validate in self.validations:
            validate(value)                  # raises on bad input
        instance.__dict__[self._name] = value
```

**The bug everyone hits — shared state.** Because the descriptor is a *class* attribute, one descriptor instance is shared by every object of the class. So storing per-object state on the descriptor itself is a shared-mutable-state bug:

```python
class Wrong:
    def __init__(self, v): self.value = v      # shared across ALL instances!
    def __set__(self, instance, value): self.value = value   # mutates everyone
```

The fix is exactly what `Field` above does: store per-instance data in `instance.__dict__[self._name]`. Two related traps: **never** call `setattr(instance, name, ...)` inside `__set__` (infinite recursion), and **avoid** descriptor→instance back-references (circular refs leak memory — use `weakref.WeakKeyDictionary` if you must map by instance).

**Data vs. non-data descriptors.** One with `__set__`/`__delete__` is a *data descriptor* and takes precedence over the instance `__dict__`; one with only `__get__` is *non-data* and is shadowed by a same-named instance attribute. This precedence rule explains a lot of "why didn't my descriptor fire?" confusion.

**When to use them.** Reserve descriptors for **generic, reusable library/framework code** with proven repetition (rule of three again). For a one-off, a plain `@property` is simpler and clearer. Keep the implementation minimal (often just `__get__`).

**Takeaway:** Descriptors are the power tool behind Python's own object model. Use them to DRY up access logic across many fields — but keep state on the instance, not the descriptor.

---

## 7 — Generators, Iterators, and Asynchronous Programming

**Why generators matter.** A function with `yield` produces values *lazily*, one at a time, suspending between them. The payoff is memory: you can process a huge (or infinite) stream without holding it all at once. Generators are also the foundation of the iterator protocol and, extended, of coroutines and async.

```python
# Before: builds the whole list in memory
def load(filename):
    out = []
    for line in open(filename):
        out.append(parse(line))
    return out

# After: lazy — constant memory, same call site
def load(filename):
    for line in open(filename):
        yield parse(line)
```

**Generator expressions** are comprehensions with parentheses, and they slot straight into consumers — drop the brackets:

```python
sum(x**2 for x in range(1000))    # not sum([...]) — no throwaway list
```

(Remember: a generator is **single-use** — exhausted after one pass.)

**Prefer generators to hand-written iterator classes.** Anything you'd write with `__iter__`/`__next__` is usually a few lines as a generator:

```python
def sequence(start=0):
    while True:
        yield start
        start += 1
```

**`itertools` and idioms.** Traverse a source once and split it with `itertools.tee`; compose lazily with `islice`/`chain`/`filter`; find the first match with `next(...)`:

```python
min_, max_, avg = itertools.tee(purchases, 3)
return min(min_), max(max_), median(avg)
```

**`yield from`** delegates to a sub-generator — it flattens nested loops and transparently forwards `send`/`throw` and the sub-generator's return value:

```python
def chain(*iterables):
    for it in iterables:
        yield from it          # replaces an inner for-loop
```

**From generators to coroutines to async.** Generators gained `send()`, `throw()`, and `close()`, turning them into coroutines that can receive values and be suspended/resumed — which became the basis for `async`. Modern code uses the dedicated syntax: `async def` + `await` (where `await` replaces `yield from` and catches awaitable/iterable mix-ups at definition time), plus `async with` (`__aenter__`/`__aexit__`) and `async for` (`__aiter__`/`__anext__`). Prefer **async generators** (`async def` + `yield`) over hand-written async iterators:

```python
async def record_streamer(max_rows):
    current = 0
    while current < max_rows:
        row = (current, await fetch())
        current += 1
        yield row
```

**Takeaway:** Reach for `yield` whenever a consumer processes items one at a time — it cuts memory, simplifies iterator code, and is the same mental model that scales up to async.

---

## 8 — Unit Testing and Refactoring

**The reframing:** tests are not an afterthought — they are **first-class code and the formal proof that your program still works after a change.** No amount of careful design (even perfect SOLID) can *guarantee* no regression; only tests can. And the deepest point of the chapter: **testability drives clean design.** If something is hard to test, that difficulty is a design smell pointing at the production code.

**What a good unit test is:** isolated (no DB/HTTP/filesystem, no dependence on other tests or order), fast, repeatable/deterministic (never flaky), and self-validating (pass/fail with no human interpretation). Scope tests to *your* boundaries — don't test third-party libraries, just verify you *call* them correctly.

**Tools.** `unittest` (stdlib, OO: `TestCase`, `assertEqual`/`assertRaises`) vs. **`pytest`** (plain functions, bare `assert`, far less boilerplate — and it runs `unittest` tests too):

```python
# unittest
class TestStatus(unittest.TestCase):
    def test_rejected(self):
        mr = MergeRequest(); mr.downvote("maintainer")
        self.assertEqual(mr.status, Status.REJECTED)

# pytest — same test, less ceremony
def test_rejected():
    mr = MergeRequest(); mr.downvote("maintainer")
    assert mr.status == Status.REJECTED
```

**Parametrize** to remove duplicated tests — one scenario per row, covering each equivalence class:

```python
@pytest.mark.parametrize("value,expected", [(2, "even"), (3, "odd")])
def test_parity(value, expected):
    assert parity(value) == expected
```

**Test doubles & the patching smell.** `Mock` records how it was called; `MagicMock` adds magic-method support; `mock.patch("module.path")` replaces something by import-path string. But **heavy `patch`ing is itself a smell** — string paths are fragile (a rename breaks them) and signal that the code clings to hard dependencies. The fix is the same refactor as DIP — inject the collaborator so the test passes a `Mock()` directly:

```python
class BuildStatus:
    def __init__(self, transport):      # inject it
        self.transport = transport
# test: BuildStatus(Mock()) — no patch strings needed
```

**Coverage is necessary but not sufficient.** A line *running* does not mean its branches were *tested* — a single test can hit 100% of a `"even" if n%2==0 else "odd"` line while never exercising the odd branch. Use coverage (`pytest-cov --cov-report term-missing`) to find blind spots; set ~80% as a floor, never a target. Go further with **property-based testing** (`hypothesis` generates counterexamples) and **mutation testing** (`mutpy` alters your code; a good suite "kills" every mutant).

**Refactoring** = changing internal structure *without* changing external behavior (signatures stay stable). It is impossible to do safely without tests — they're the "external observer" that proves the contract held. Use **TDD** (red → green → refactor) for new features and for reproducing a bug before fixing it. And treat test code with the same care (DRY, readability) as production code.

**Takeaway:** Let testability *lead* design. If a test is painful to write, fix the code — inject dependencies, split functions — and you'll improve both at once.

---

## 9 — Common Design Patterns

**The Pythonic caveat first.** GoF design patterns are high-level, language-agnostic ideas. But Python's dynamism — first-class functions and classes, no `new`, duck typing — makes several patterns **invisible (built in) or unnecessary.** The iterator pattern is the `for` protocol; "strategy" is just passing a function. So the rule is: **let patterns emerge from refactoring; never force one.** And don't name a class after its pattern (`EnhancedQuery`, not `EnhancedQueryDecorator`) — that leaks implementation and violates intention-revealing naming.

**Creational**
- **Factory:** usually just a plain function that builds the object (optionally taking the class as a parameter) — no factory-class hierarchy needed.
- **Singleton:** *avoid it.* A singleton is an OO global — mutable from anywhere, side-effect-prone, hard to test. If you need one shared thing, use a **module-level object** (modules are singletons). If you need synchronized state, prefer **monostate / shared-state** (a class variable behind a property → a descriptor → the Borg idiom, in increasing power and risk).

**Structural**
- **Adapter & the Decorator pattern** (distinct from `@` syntax): build them with **composition + duck typing** — the enhancer just needs the same method name, no shared base class:
  ```python
  class RemoveEmpty:
      def __init__(self, query): self.decorated = query
      def render(self):
          return {k: v for k, v in self.decorated.render().items() if v}
  # CaseInsensitive(RemoveEmpty(original)).render()   # chainable at runtime
  ```
- **Composite:** leaves and containers share one interface; the container recurses into its children (e.g. a `price` property that sums sub-prices).
- **Facade:** a single entry point in front of a tangled subsystem — reduces O(N²) connections and lets you refactor behind a stable surface. A package's `__init__.py` is a facade; even `os` is a facade over `posix`/`nt`.

**Behavioral**
- **Chain of responsibility:** handlers each with `can_process` + a `successor`, assembled at runtime so precedence is dynamic.
- **Command:** separate *defining* an action from *executing* it (think building a SQL query, then running it).
- **State:** *reify* a status flag into real state objects sharing an interface; the context delegates to its current state, which performs transitions — this kills the giant `if status == ...` switch.
- **Null Object:** always return a *consistent type*. Never return `None` where callers expect an object (they'll hit `AttributeError`); return an empty container or a domain null object (e.g. `UnknownUser`) that honors the interface as no-ops.

**Takeaway:** Patterns are vocabulary for solutions you *discover*, not blueprints you impose. In Python, the cleanest implementation is usually the one that leans on first-class functions and duck typing.

---

## 10 — Clean Architecture

**The big idea:** good architecture is clean code *scaled up*. The same principles — cohesion, single responsibility, intention-revealing names, depend-on-abstractions — reappear unchanged at the level of packages and services. Clean code is the **cornerstone**: no architecture survives careless code beneath it.

**Separation of concerns at scale.** A *component* is any independently releasable unit. In Python that's either a **package** (in-process — efficient, but rigid and deployed as one) or a **microservice** (fully decoupled, polyglot, independently deployable — at the cost of network latency and the need for hard contracts). Choosing between them is a deliberate trade-off, not a default. Avoid the all-knowing monolith that's the single source of truth for everything.

**The dependency rule — the heart of the chapter.** Dependencies must flow **one direction only: inward, toward the domain / business kernel.** You can verify this just by reading `import` statements — the application imports `storage` and `web`; they never import the application.

**Keep the domain pure.** Domain models are *plain business objects* — no ORM entities, no framework base classes. Crucially, **an ORM is a dependency, not an abstraction** (it's code you don't control). Wrap it in an *adapter* layer that emits *your* domain objects; the domain must not know the ORM exists (this is hexagonal architecture). The litmus test — you should **not be able to tell from the visible application code which database or web framework is in use**:

```python
class DeliveryView(View):
    async def _get(self, request, delivery_id: int):
        query = DeliveryStatusQuery(int(delivery_id), await DBClient())
        try:
            result = await query.get()
        except OrderNotFoundError as e:
            raise NotFound(str(e)) from e
        return result.message()
# Nothing here names a DB, ORM, or framework — swapping any of them
# only changes an adapter, as long as get() still returns a domain object.
```

A "screaming" architecture screams the *domain*, not the technology.

**Operational discipline.** Pin dependency *ranges* (`>=X,<next-major`) and compile a full, version-controlled `requirements.txt` (`pip-tools`), then install from it in the Dockerfile for reproducible builds. Host an **internal artifact repository** — don't bet your build on public PyPI's availability (remember `left-pad`). Treat stale dependencies as technical debt (they carry unpatched security holes). Ship services as Docker containers. And follow the **testing pyramid**: many fast unit tests on pure domain objects, fewer component tests with mocked dependencies, fewest (but still mandatory) integration tests.

**Book-wide close.** The same ideas recur at every scale and all interlock — abstraction, layering, dependency inversion, separation of concerns, all flowing from *intention-revealing code in terms of the domain*. Cohesion and SRP are what decide when to carve out a package or a service. But above all: **principles, not laws** — it won't always be possible or worthwhile to fully abstract every dependency, and that's fine. *Practicality beats purity.* The book's true aim is to sharpen your judgment, not to hand you recipes — idioms change, but the reasoning endures.

---

## Cheat Sheet

**Mindset:** Code communicates to humans. Optimize for readability and changeability. Linting is the floor, not the goal. *Practicality beats purity.*

| When you see… | Reach for… |
|---|---|
| `try/finally` cleanup | `with` context manager |
| `for … append()` | comprehension / generator |
| bare or empty `except` | specific exception + real handling; `raise … from e` |
| mutable default arg (`=[]`/`={}`) | `None` sentinel |
| `__init__` of pure assignments | `@dataclass` |
| `get_x` / `set_x` | attribute or `@property` |
| growing `if/elif` dispatch | polymorphism (OCP) |
| dependency built inside `__init__` | inject it (DIP) |
| god-class | split by responsibility (SRP) |
| inheritance for reuse | composition |
| `# type: ignore` on an override | fix the LSP violation |
| decorator wrapper w/o `functools.wraps` | add `@wraps` |
| repeated `@property` validation | one reusable descriptor |
| list returned but consumed once | generator (`yield`) |
| nested `for` forwarding values | `yield from` |
| heavy `mock.patch(...)` | inject dependencies |
| chasing 100% coverage | find blind spots; add edge/equivalence tests |
| singleton | module object / monostate |
| `return None` where an object is expected | null object / consistent type |
| ORM model used as a domain object | adapter → pure domain object |
| domain importing infrastructure | invert: dependencies point inward |

**The five acronyms:** DRY (one place per fact) · YAGNI (no speculative code) · KIS (smallest solution) · EAFP > LBYL · SOLID.

**The rule of three:** don't abstract (a decorator, descriptor, pattern, or package) until the need has genuinely repeated three times.

---

*Companion: [Python Refactoring Checklist](clean-code-python-refactoring-checklist.md) — every smell above with a full definition, fix, and before→after code. Source: Mariano Anaya, *Clean Code in Python*, 2nd ed. (Packt, 2021).*
