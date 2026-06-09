# Python Refactoring Checklist

> A standalone, actionable refactoring reference distilled from *Clean Code in Python* (2nd ed., Mariano Anaya). For the full learning guide see [clean-code-in-python.md](clean-code-in-python.md).
>
> **How to use:** Each entry has a **Smell** (what it is and why it hurts) and a **Fix** (what to do), most with a *before → after* snippet. Walk a file (or a diff) top to bottom against the relevant sections. Treat each item as a yes/no gate; if it triggers, apply the fix or write down why you consciously skipped it. Remember the overarching rule: **principles, not laws — practicality beats purity.**

## Contents
- [A. Formatting, Comments & Documentation](#a-formatting-comments--documentation)
- [B. Pythonic Idioms](#b-pythonic-idioms)
- [C. Robustness & Design Traits](#c-robustness--design-traits)
- [D. SOLID Principles](#d-solid-principles)
- [E. Decorators](#e-decorators)
- [F. Descriptors](#f-descriptors)
- [G. Generators, Iteration & Async](#g-generators-iteration--async)
- [H. Testing & Refactoring](#h-testing--refactoring)
- [I. Design Patterns](#i-design-patterns)
- [J. Architecture](#j-architecture)

---

## A. Formatting, Comments & Documentation

### A1. Commented-out code
- [ ] **Smell:** Dead code left behind in comments. It pollutes the "knowledge" the codebase represents, drifts out of sync, and creates contradictions for the next reader. Code is the ultimate expression of design — commented-out code is noise pretending to be signal.
- **Fix:** Delete it. Version control already remembers it; recover from history if ever needed.

### A2. A comment that explains *what* the code does
- [ ] **Smell:** A comment narrating mechanics is "a symptom of our inability to express the code correctly." It will rot as the code changes.
- **Fix:** Make the code self-documenting — rename variables, extract a well-named function. Reserve comments for non-obvious *why* (e.g. working around an upstream bug).
```python
# Before
x = d * 0.9  # apply the loyalty discount

# After
discounted_price = base_price * LOYALTY_DISCOUNT_FACTOR
```

### A3. No automated quality gate
- [ ] **Smell:** Style/type consistency is enforced by humans in code review — slow, subjective, and inconsistent. Formatting is necessary but should never consume review time.
- **Fix:** Wire up `black` (format), `mypy` (types), `pylint`/`flake8` (lint), and a `make checklist` target that runs all three plus tests. **Fail the CI build** on any violation so quality is objective.

### A4. A magic primitive type repeated everywhere
- [ ] **Smell:** A bare `float`/`int`/`str` used to mean a domain concept (seconds, a client record) gives readers no information and has no single place to change.
- **Fix:** Introduce a named type alias as a one-place abstraction.
```python
# Before
def launch_task(delay: float): ...

# After
Seconds = float
def launch_task(delay: Seconds): ...
```

### A5. A function whose parameter types / return aren't obvious
- [ ] **Smell:** In a dynamically typed language a reader can't tell what to pass or what comes back, especially for nested/dynamic data.
- **Fix:** Add type annotations (help readers *and* mypy), and a docstring with an *example* of the expected input/output. Annotations give types; the docstring gives intent and shape.

---

## B. Pythonic Idioms

### B1. Manual index loop to slice a list
- [ ] **Smell:** C-style index arithmetic (`xs[len(xs)-1]`, manual loops to drop elements) is verbose and error-prone.
- **Fix:** Use slice syntax and negative indices (end is excluded).
```python
last = xs[-1]
middle = xs[1:-1]
every_other = xs[::2]
```

### B2. `try/finally` around resource use
- [ ] **Smell:** Manual acquire/release is easy to get wrong (forgotten `close()` on an early return or exception).
- **Fix:** Use a `with` context manager.
```python
# Before
fd = open(path)
try:
    process(fd)
finally:
    fd.close()
# After
with open(path) as fd:
    process(fd)
```

### B3. Repeated pre/post logic not tied to an object
- [ ] **Smell:** The same setup/teardown bracket copy-pasted around different blocks; or a `try/except: pass` that silently ignores a known exception.
- **Fix:** Extract a `@contextlib.contextmanager` generator. For "ignore this known error," use `contextlib.suppress(Err)` to make the intent explicit.
```python
import contextlib
with contextlib.suppress(FileNotFoundError):
    os.remove(path)
```

### B4. Empty container + `for … append()`
- [ ] **Smell:** Building a collection imperatively is more code and slower (multiple Python operations) than the declarative form.
- **Fix:** Use a comprehension (list/set/dict) or a generator expression.
```python
# Before
result = []
for i in range(10):
    result.append(run(i))
# After
result = [run(i) for i in range(10)]
```

### B5. A function recomputed inside a comprehension / multi-statement build-up
- [ ] **Smell:** Calling the same function twice, or several statements that exist only to feed a filter/map.
- **Fix:** Bind once with a walrus `:=` (Python 3.8+).
```python
# After
return {
    match.group("id")
    for line in lines
    if (match := re.match(PATTERN, line)) is not None
}
```

### B6. Hand-written `get_x` / `set_x`
- [ ] **Smell:** Java-style accessors for plain data add noise; Python attributes are already public.
- **Fix:** Use a plain attribute, or `@property` + `@x.setter` only when retrieval/assignment needs logic or validation. Keep getters side-effect-free (a lazy property is the rare exception). Supports command/query separation.
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

### B7. `__x` used for "privacy"
- [ ] **Smell:** Double-underscore is mistaken for "private." It actually triggers *name mangling* (`_Class__x`) to avoid subclass attribute collisions — and breaks code that tries to access it.
- **Fix:** Use a single leading underscore `_x` (the convention for "internal, not the public interface"). Don't invent your own dunder (`__like_this__`) attributes either.

### B8. `__init__` that only assigns constructor args to attributes
- [ ] **Smell:** Boilerplate `self.a = a; self.b = b` for a data-holding class.
- **Fix:** Use `@dataclass`. Use `field(default_factory=...)` for mutable defaults and `__post_init__` for validation/derived values. (Annotations don't *enforce* types — cast in init logic if needed.)
```python
from dataclasses import dataclass, field
@dataclass
class Node:
    value: int
    children: list = field(default_factory=list)
```

### B9. Mutable default argument
- [ ] **Smell:** `def f(x={})` / `=[]` — the default is created **once** at definition time and shared/mutated across all calls, causing spooky bugs and memory leaks.
- **Fix:** Use a `None` sentinel and create the real default inside the body.
```python
# Before
def add_item(item, items=[]):    # shared list!
    items.append(item); return items
# After
def add_item(item, items=None):
    items = items if items is not None else []
    items.append(item); return items
```

### B10. Subclassing a built-in collection
- [ ] **Smell:** `class X(dict/list/str)`. In CPython the built-in methods don't call your overrides, so behavior is inconsistent (e.g. iteration ignores your `__getitem__`).
- **Fix:** Subclass `collections.UserDict`, `UserList`, or `UserString` for portable, correct override behavior.

### B11. Verbose / duplicated boundary or membership checks
- [ ] **Smell:** Inline conditions like `if 0 <= c.x < w and 0 <= c.y < h:` repeated across the codebase.
- **Fix:** Implement `__contains__` on a cohesive collaborator and use the `in` operator.
```python
# After
if coord in grid:
    grid[coord] = MARKED
```

### B12. One-shot iterable
- [ ] **Smell:** A class whose `__iter__` returns `self`; after one `for` loop it's exhausted and silently yields nothing on the second pass.
- **Fix:** Make `__iter__` a generator (or return a fresh iterator) so each iteration starts over (a "container iterable").

---

## C. Robustness & Design Traits

### C1. Empty / bare `except`
- [ ] **Smell:** `except:` or `except Exception: pass` — the "most diabolical Python anti-pattern." Violates "errors should never pass silently"; swallows bugs and masks real failures.
- **Fix:** Catch a *specific* exception **and** do real handling — log (`logger.exception`), substitute a safe value, or re-raise. Use `contextlib.suppress(SpecificError)` if ignoring is genuinely intended. Configure CI to flag bare excepts.

### C2. Re-raising a different exception without the cause
- [ ] **Smell:** `raise CustomError(...)` inside an `except` loses the original traceback, hiding the root cause.
- **Fix:** Chain the cause with `from` (PEP-3134).
```python
try:
    return data[key]
except KeyError as e:
    raise InternalDataError("Record not present") from e
```

### C3. `except Exception` (too broad)
- [ ] **Smell:** Catching everything hides unrelated bugs and makes the failure mode unclear.
- **Fix:** Narrow to the precise type(s) you actually expect (`KeyError`, `ValueError`, …).

### C4. Exceptions used as control flow
- [ ] **Smell:** Raising/catching exceptions to implement normal business branches. Hurts readability, breaks encapsulation, and spreads logic across the call stack.
- **Fix:** Use ordinary control flow (`if`/return values). Reserve exceptions for genuinely exceptional situations the caller must be told about.

### C5. One handler mixing exceptions of different abstraction levels
- [ ] **Smell:** A single `try` handling both a low-level `ConnectionError` and a domain `ValueError` — the function is doing too much and the handling doesn't match its abstraction level.
- **Fix:** Handle each exception at the layer it belongs to; extract helpers (e.g. a `connect_with_retry`) so each concern lives where it makes sense.

### C6. A function that raises many exceptions
- [ ] **Smell:** Lots of distinct failure modes = the function is not cohesive / not context-free.
- **Fix:** Treat it as a cohesion smell and split the function into smaller, single-purpose ones.

### C7. Traceback / sensitive info reaching end users
- [ ] **Smell:** Leaking stack traces or internal data is an information-disclosure risk.
- **Fix:** Log full detail internally; show a generic message externally ("Something went wrong").

### C8. `assert` misused
- [ ] **Smell:** `assert` with a side-effecting call (`assert do_thing()`), or asserts used for business logic / input validation. Asserts are stripped by `python -O`, and a function call inside one isn't repeatable when debugging.
- **Fix:** Use assertions only for *impossible* conditions (correctness self-checks/invariants). Evaluate first into a local, then assert with a descriptive message. Never catch `AssertionError`; never run production with `-O`. Use exceptions for expected/business errors.
```python
# Before
assert condition.holds(), "not satisfied"
# After
result = condition.holds()
assert result, f"unexpected state: {result}"
```

### C9. Mutating a function argument
- [ ] **Smell:** Changing a passed-in mutable object creates hidden side effects for the caller.
- **Fix:** Copy and return a new modified value; keep functions side-effect-free.

### C10. Reading keys out of `**kwargs`
- [ ] **Smell:** `kwargs.get("timeout", DEFAULT)` hides the real signature; callers can't tell what's accepted.
- **Fix:** Declare the parameters explicitly (with defaults) in the signature.
```python
# Before
def f(**kwargs): timeout = kwargs.get("timeout", DEFAULT)
# After
def f(timeout=DEFAULT, **kwargs): ...
```

### C11. Too many parameters
- [ ] **Smell:** A long parameter list (pylint warns). High coupling to the caller, and the function is a leaky abstraction (caller must know all its internals). Often the args all come from one object.
- **Fix:** *Reify* — pass the object that already groups them, or create the missing abstraction. If the body does many different things based on params, split the function. Don't just suppress the warning.
```python
# Before
track_request(request.headers, request.ip_addr, request.request_id)
# After
track_request(request)
```

### C12. Bare `*args, **kwargs` signature
- [ ] **Smell:** A signature that accepts anything loses meaning and legibility.
- **Fix:** Restore explicit named arguments. Keep `*args, **kwargs` only for *true* wrappers — decorators, `super()` calls, perfect proxies.

### C13. An ambiguous boolean / context-dependent parameter
- [ ] **Smell:** `f(data, True)` at the call site — unreadable, and adding a flag positionally breaks back-compat.
- **Fix:** Make it keyword-only (place after `*`) for explicitness and backward-compatible extension.
```python
def f(data, *, use_new_impl=False): ...
f(data, use_new_impl=True)
```

### C14. Duplicated logic / unnamed domain knowledge (DRY/OAOO)
- [ ] **Smell:** The same expression or rule appears in more than one place. A change now needs editing in multiple spots — error-prone, with no single source of truth.
- **Fix:** Name the knowledge once — extract a function/object/context manager/decorator/iterator. Centralize shared constants in one module.
```python
# After
def score_for_student(s):
    return s.passed * 11 - s.failed * 5 - s.years * 2
sorted(students, key=score_for_student)
```

### C15. Premature abstraction for imagined futures (YAGNI)
- [ ] **Smell:** Base classes, hooks, config, or patterns added "in case we need it." The speculative abstraction is usually wrong and harder to refactor than no abstraction.
- **Fix:** Build only for current requirements, in a way that's easy to change later. Add abstractions bottom-up when the need actually appears.

### C16. Over-engineered / metaprogramming-heavy code (KIS)
- [ ] **Smell:** Metaclasses, deep hierarchies, or clever machinery where something simple would do.
- **Fix:** Simplify to the minimal solution and the smallest stdlib data structure that fits. A little duplication can beat a complicated abstraction.

### C17. `if exists(...)`-style pre-checks (LBYL)
- [ ] **Smell:** Checking conditions before acting is verbose and races with the actual operation.
- **Fix:** Prefer EAFP — just do it and catch the exception. More intention-revealing in Python (no performance penalty).
```python
# Before (LBYL)
if os.path.exists(path):
    with open(path) as f: ...
# After (EAFP)
try:
    with open(path) as f: ...
except FileNotFoundError:
    ...
```

### C18. Inheritance used for code reuse / extending a data structure
- [ ] **Smell:** A subclass that overrides/replaces most inherited methods, or a domain class extending `dict`/`list`/`UserDict` just to reuse it — it inherits unwanted public methods (`pop`, `items`) and asserts a false "is-a."
- **Fix:** Use **composition** — hold the collaborator privately and proxy only the methods you need.
```python
# Before
class TransactionalPolicy(collections.UserDict): ...
# After
class TransactionalPolicy:
    def __init__(self, data): self._data = dict(data)
    def __getitem__(self, k): return self._data[k]
    def change_in_policy(self, cid, **kw): self._data[cid].update(**kw)
```

### C19. A large file with many unrelated definitions
- [ ] **Smell:** A god-module that's hard to navigate, imports a lot, and loads many objects into memory.
- **Fix:** Split into a package — a directory with `__init__.py` that re-imports the public names (preserving compatibility), grouping logic by similarity. Expose the public surface via `__all__`.

---

## D. SOLID Principles

### D1. (S) A class with unrelated method clusters / multiple reasons to change
- [ ] **Smell:** A god-class (e.g. one that loads data *and* parses it *and* sends it). Methods partition into unrelated groups; reusing one drags in the rest.
- **Fix:** Single Responsibility — split each cluster into its own cohesive class and compose them as collaborators. (Not "one method per class" — methods serving one responsibility belong together.)

### D2. (O) Growing `if/elif` dispatch
- [ ] **Smell:** Every new case requires editing the same method — it's not closed for modification, risking ripple effects.
- **Fix:** Open/Closed — replace the conditional with polymorphism: a base abstraction plus one subclass per case implementing a shared method. Discover cases via `__subclasses__()` or a registry. Success test: a new feature touches only *new* code.
```python
# After
class LoginEvent(Event):
    @staticmethod
    def meets_condition(data): return data["before"].get("session") == 0 ...

def identify_event(self):
    for cls in Event.__subclasses__():
        if cls.meets_condition(self.data):
            return cls(self.data)
    return UnknownEvent(self.data)
```

### D3. (L) A subclass that isn't substitutable
- [ ] **Smell:** A subclass changes a parameter/return type, strengthens a precondition (requires data the base doesn't), or weakens a postcondition — so clients break when given the subtype. mypy flags type breaks; pylint flags `arguments-differ`.
- **Fix:** Liskov Substitution — restore the contract so any subtype is a drop-in replacement (e.g. `event_data["x"]` → `event_data.get("x")`). **Never** silence the tool with `# type: ignore`; it's reporting a real design flaw.

### D4. (I) A fat interface
- [ ] **Smell:** One interface bundling disjoint capabilities (`from_xml` + `from_json`), forcing implementers to `pass` on methods they don't use — which also makes them violate SRP.
- **Fix:** Interface Segregation — split into cohesive ABCs (`@abstractmethod`), one capability each; recombine via multiple inheritance only where genuinely needed. Don't over-segregate methods that must operate together (`__enter__`/`__exit__`).

### D5. (D) A high-level class constructing a concrete collaborator
- [ ] **Smell:** A high-level class hard-codes/instantiates a low-level concrete class (especially external/volatile ones). It must change whenever that detail changes, and it's painful to test.
- **Fix:** Dependency Inversion — depend on an abstract interface (ABC) and **inject** the concrete implementation through `__init__`. *"Don't force the creation of dependencies in the initialization method."*
```python
# Before
class EventStreamer:
    def __init__(self): self._target = Syslog()
# After
class EventStreamer:
    def __init__(self, target: DataTargetClient): self._target = target
```

---

## E. Decorators

### E1. Wrapper without `functools.wraps`
- [ ] **Smell:** The wrapper replaces the original's `__name__`, `__qualname__`, `__doc__`, `__annotations__` — every decorated function becomes `wrapped`, breaking `help()`, logging, and doctests.
- **Fix:** Apply `@functools.wraps(func)` to the inner wrapper.
```python
def trace(function):
    @functools.wraps(function)
    def wrapped(*args, **kwargs):
        logger.info("running %s", function.__qualname__)
        return function(*args, **kwargs)
    return wrapped
```

### E2. Side effects in the decorator body
- [ ] **Smell:** Code outside the inner `wrapped` runs at *import/decoration time*, not call time — e.g. a timer that starts at import, or an external call before config is loaded.
- **Fix:** Move all side effects inside `wrapped` so they run per call. Exception: intentional import-time work (e.g. a `register_event` registry) stays in the outer scope, documented.

### E3. 3+ nested closures for a parameterized decorator
- [ ] **Smell:** Deep indentation (outer args → decorator → wrapped) is hard to read and stateless.
- **Fix:** Implement the decorator as a class — params in `__init__`, logic in `__call__`. More readable, and it can hold state. Make decorator args keyword-only.
```python
class WithRetry:
    def __init__(self, retries=3): self.retries = retries
    def __call__(self, op):
        @functools.wraps(op)
        def wrapped(*a, **kw):
            for _ in range(self.retries):
                try: return op(*a, **kw)
                except ControlledException as e: last = e
            raise last
        return wrapped
```

### E4. A decorator that breaks on methods
- [ ] **Smell:** A decorator written for a plain function fails on a method because of the extra `self` (`TypeError: takes 1 positional argument but 2 were given`).
- **Fix:** Use generic `*args, **kwargs`, or implement the decorator as a class with `__get__` (descriptor protocol) that rebinds via `types.MethodType`.

### E5. A one-off decorator with little reuse
- [ ] **Smell:** A decorator built before the pattern has emerged adds indirection that doesn't pay off.
- **Fix:** Apply the **rule of three** — only extract a decorator once the pattern repeats ≥3 times. Keep each to a single responsibility (split log vs. time and stack them). Otherwise use a plain function or small class.

---

## F. Descriptors

> Descriptors are advanced and belong in reusable library/framework code, not business logic. Use them only with proven repetition.

### F1. Repeated `@property` boilerplate
- [ ] **Smell:** The same validation/transformation logic copy-pasted across many `@property` setters.
- **Fix:** Factor it into one reusable descriptor placed as a class attribute.
```python
class Field:
    def __set_name__(self, owner, name): self._name = name
    def __get__(self, instance, owner):
        if instance is None: return self
        return instance.__dict__[self._name]
    def __set__(self, instance, value):
        for validate in self.validations: validate(value)
        instance.__dict__[self._name] = value
```

### F2. Per-instance state stored on the descriptor itself
- [ ] **Smell:** `self.value = ...` inside the descriptor. Because descriptors are class attributes shared by all instances, this is a shared-state bug — setting one instance changes them all.
- **Fix:** Store/retrieve per-instance data in `instance.__dict__[self._name]`; use `__set_name__` to learn the attribute name.

### F3. `setattr(instance, name, value)` inside `__set__`
- [ ] **Smell:** Assigning the attribute back on the instance re-triggers the descriptor → infinite recursion.
- **Fix:** Write directly to `instance.__dict__[self._name]`.

### F4. Descriptor → instance back-references
- [ ] **Smell:** Mapping descriptor → client instances creates circular references; objects never get garbage-collected (memory leak).
- **Fix:** Prefer the `instance.__dict__` approach; if you must key by instance, use `weakref.WeakKeyDictionary`.

### F5. A descriptor for a one-off case
- [ ] **Smell:** Reaching for the descriptor protocol where a single property would do — unjustified complexity.
- **Fix:** Use a plain `@property`. Reserve descriptors for generic, reusable, proven-repeated logic; implement the minimal protocol (often just `__get__`).

---

## G. Generators, Iteration & Async

### G1. Function building & returning a full list consumed one-at-a-time
- [ ] **Smell:** The whole list is materialized in memory even though the caller processes items sequentially.
- **Fix:** Make it a generator with `yield` — lazy, constant-memory.
```python
# Before
def load(fn):
    out = []
    for line in open(fn): out.append(parse(line))
    return out
# After
def load(fn):
    for line in open(fn): yield parse(line)
```

### G2. List comprehension passed to `sum`/`min`/`max`/`any`/`all`
- [ ] **Smell:** `sum([x**2 for x in xs])` builds a throwaway list first.
- **Fix:** Drop the brackets — pass a generator expression. `sum(x**2 for x in xs)`.

### G3. Hand-written iterator class
- [ ] **Smell:** A class implementing `__iter__`/`__next__` where a generator would be far simpler.
- **Fix:** Replace with a generator function. Also: design objects for iteration with `__iter__` rather than relying on the `__getitem__`/`__len__` sequence fallback.

### G4. Looping over the same data more than once
- [ ] **Smell:** Re-iterating an iterable (or re-reading a source) to compute several aggregates.
- **Fix:** Use `itertools.tee` to split it once; compose lazily with `islice`, `chain`, `filter`.
```python
min_, max_, avg = itertools.tee(purchases, 3)
return min(min_), max(max_), median(avg)
```

### G5. Nested `for` loops / break-flags to find an item
- [ ] **Smell:** Manual nested loops with a found-flag, or exceptions used to break out.
- **Fix:** Flatten into a helper generator and use `next(...)`, converting `StopIteration` into a domain error.
```python
try:
    coord = next(c for (c, cell) in iter_2d(grid) if cell == target)
except StopIteration as e:
    raise ValueError(f"{target} not found") from e
```

### G6. Nested `for` only forwarding values
- [ ] **Smell:** `for it in iterables: for v in it: yield v`.
- **Fix:** Delegate with `yield from it` — also forwards `send`/`throw` and captures sub-generator return values.

### G7. Old-style coroutines / hand-written async iterators
- [ ] **Smell:** `@coroutine` + `yield from` for async, or full `__aiter__`/`__anext__` classes.
- **Fix:** Use `async def` + `await` (mismatches are caught at definition time). Prefer **async generators** (`async def` + `yield`) over hand-written async iterators; use `async with`/`async for`.

---

## H. Testing & Refactoring

### H1. Code that's hard to test / needs heavy patching
- [ ] **Smell:** "Good software is testable software." If a unit needs convoluted setup or lots of monkey-patching to test, that's a design smell.
- **Fix:** Refactor first — inject dependencies, split large functions, extract side-effecting/conversion logic into a testable adapter.

### H2. `mock.patch("module.path.Thing")` everywhere
- [ ] **Smell:** String-path patching is fragile (breaks on rename/move), makes tests shallow, and signals the code is over-reliant on hard dependencies.
- **Fix:** Inject collaborators so a test can pass a `Mock()` directly — no string paths.
```python
# After
class BuildStatus:
    def __init__(self, transport): self.transport = transport
# test: BuildStatus(Mock())
```

### H3. Hard-coded dependency built in `__init__`
- [ ] **Smell:** Constructing a concrete collaborator inside `__init__` couples the class and forces patching to test.
- **Fix:** Inject it; extract conversion/side-effecting logic into an adapter/wrapper that's independently testable.

### H4. Duplicated near-identical tests
- [ ] **Smell:** Several tests differing only in input/expected data.
- **Fix:** `@pytest.mark.parametrize` — one scenario per row, cover every equivalence class, label cases for debuggability (one test case generated per row).
```python
@pytest.mark.parametrize("value,expected", [(2, "even"), (3, "odd")])
def test_parity(value, expected):
    assert parity(value) == expected
```

### H5. `Mock` raising "object is not subscriptable / callable"
- [ ] **Smell:** The code under test uses magic methods (`__getitem__`, `__len__`) that plain `Mock` doesn't support.
- **Fix:** Use `MagicMock`.

### H6. Real side effects leaking from unit tests
- [ ] **Smell:** Unit tests hit the network/DB/filesystem — slow, flaky, not isolated.
- **Fix:** Add an `autouse` fixture in `conftest.py` that patches out external calls (e.g. all HTTP) across the test dir. Keep unit tests isolated, fast, repeatable, self-validating.

### H7. Refactoring without tests
- [ ] **Smell:** Changing internal structure with no safety net — you can't prove behavior is preserved.
- **Fix:** Write characterization tests first (the "external observer"); keep public methods and signatures stable while refactoring; re-run after every change.

### H8. Chasing 100% coverage
- [ ] **Smell:** Coverage treated as a goal. A line *running* doesn't mean its branches/conditions were *tested* — 100% can hide untested logic.
- **Fix:** Use coverage (`pytest-cov --cov-report term-missing`) to find blind spots; set ~80% as a floor, not a target. Add boundary-value, edge-case, and equivalence-class tests; strengthen with `hypothesis` (property-based) and `mutpy` (mutation testing).

### H9. New feature or bug fix without a test-first approach
- [ ] **Smell:** Writing implementation before tests; bug fixed without a regression test.
- **Fix:** TDD — failing test (red) → minimal code to pass (green) → refactor (improve). For bugs, reproduce with a failing test before fixing.

---

## I. Design Patterns

> Patterns should *emerge* from refactoring, not be forced. Python's dynamism makes several GoF patterns invisible/unnecessary.

### I1. A factory class for simple construction
- [ ] **Smell:** A factory-class hierarchy for object creation — over-engineered in Python (classes/functions are first-class, no `new`).
- **Fix:** Use a plain function as the factory (optionally taking the target class as a parameter). Use a DI library only for complex graphs.

### I2. A singleton
- [ ] **Smell:** A singleton is an OO global variable — mutable from anywhere, side-effect-prone, hard to unit test.
- **Fix:** Avoid it. For one shared thing, use a **module-level object** (modules are singletons via `sys.modules`). For synchronized state, prefer **monostate/shared-state** (class var → descriptor → Borg, increasing power/risk).

### I3. Inheritance for adapter / decorator / strategy
- [ ] **Smell:** Inheriting from a base purely to reuse/extend behavior — adds coupling and a false "is-a."
- **Fix:** Use **composition + duck typing**: the enhancer just needs the same method name (`render()`), no shared base class. For "strategy," just pass a function.

### I4. A class full of `if status == …` branches
- [ ] **Smell:** Behavior switches on a status flag/enum scattered through many `if`s — hard to extend, error-prone.
- **Fix:** *Reify* into **State** objects sharing one interface (an ABC); the context delegates to its current state object, which performs the transition. Leave intentional no-op methods explicit.

### I5. Returning `None` where callers expect an object
- [ ] **Smell:** Callers must null-check everywhere; a missed check yields `AttributeError: 'NoneType' …`.
- **Fix:** Return a consistent type — an empty container (`{}`) or a **null object** (e.g. `UnknownUser`) that honors the real interface with no-op methods. Avoid a generic "do-anything" Mock-like null (it hides bugs).

### I6. A many-to-many tangle of objects
- [ ] **Smell:** O(N²) interconnections; objects know too much about each other; internals can't be refactored freely.
- **Fix:** Introduce a **Facade** — a single entry point in front of the subsystem. A package's `__init__.py` is a facade; keep internals importable only through it.

### I7. A class named after its pattern
- [ ] **Smell:** `EnhancedQueryDecorator`, `OrderFactory` — naming leaks implementation and violates intention-revealing naming.
- **Fix:** Name for intent (`EnhancedQuery`); mention the pattern in the docstring only. The best designs make the pattern transparent to users.

---

## J. Architecture

### J1. Domain objects that are ORM models / framework subclasses
- [ ] **Smell:** Business logic depends on `Model`/framework base classes. The ORM is a dependency you don't control, leaking into the core.
- **Fix:** Make domain models **pure business objects**. Wrap the ORM in an **adapter** layer that emits your domain objects (hexagonal architecture); the domain must not know the ORM exists.

### J2. Dependencies pointing outward
- [ ] **Smell:** The domain/application layer imports infrastructure (storage, web, framework) — the dependency arrow points the wrong way.
- **Fix:** Invert it — dependencies flow **one direction only, inward toward the business kernel**. The app imports `storage`/`web`; they never import the app. Verify via `import` statements.

### J3. Framework / DB names visible in application code
- [ ] **Smell:** You can tell from the code which DB, ORM, or web framework is used — the details aren't abstracted.
- **Fix:** Hide them behind adapters/interfaces. Goal: a "screaming" architecture that screams the *domain*, not the technology. Swapping the framework should change only adapters.

### J4. Packages/components named by technology
- [ ] **Smell:** `postgres_utils`, `flask_layer` — names describe the tech, not the purpose.
- **Fix:** Name by intent (`storage`, `web`).

### J5. Unpinned / floating dependencies
- [ ] **Smell:** Loose or unspecified versions → non-reproducible builds, surprise breakages.
- **Fix:** Pin version *ranges* (`>=X,<next-major`); compile a full transitive `requirements.txt` (`pip-tools`/`pip-compile`) under version control; install from it in the Dockerfile for deterministic builds.

### J6. Stale dependencies
- [ ] **Smell:** Old pinned versions accumulate — missing features and, critically, unpatched security vulnerabilities. This is technical debt.
- **Fix:** Upgrade continuously; automate upgrade PRs (Dependabot-style) and security scans. Host an internal artifact repo so you don't depend on public PyPI availability (cf. `left-pad`).

### J7. One all-knowing monolith
- [ ] **Smell:** A single codebase is the source of truth for everything — changes can't be isolated, tested, or deployed independently.
- **Fix:** Split into independently releasable **components**, choosing consciously between a Python **package** (in-process, efficient, rigid) and a **microservice** (decoupled, polyglot, independently deployable, network-latency cost). Follow the testing pyramid: many unit, fewer component, fewest integration.

---

*Source: Mariano Anaya, *Clean Code in Python*, 2nd edition (Packt, 2021). Synthesized from a full read of all ten chapters. Companion learning guide: [clean-code-in-python.md](clean-code-in-python.md).*
