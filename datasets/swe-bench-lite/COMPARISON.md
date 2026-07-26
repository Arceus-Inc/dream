# dream vs opencode — SWE-bench Lite (25 tasks)

Model: **gpt-5.2** (same endpoint for both) · in-container (real test env) · official SWE-bench Docker grading.

## Headline

| harness | resolved | avg tokens | avg in | avg out | avg time | avg steps | empty | errors |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| dream | 19/25 (76.0%) | 513836 | 509410 | 4426 | 208s | 1.2 | 1 | 0 |
| opencode | 21/25 (84.0%) | 431471 | 23247 | 1940 | 202s | 15.3 | 0 | 0 |

- **Resolve rate** = official SWE-bench resolution (FAIL_TO_PASS flip + PASS_TO_PASS hold).
- **avg tokens** = mean total tokens/task. dream total = input+output; opencode total includes reasoning + cached-context reads (per-turn billed). Token accounting differs slightly between harnesses — treat as indicative.
- `·` ran but not resolved · `—` not attempted · `✓` resolved.

## Per-task

| instance | dream | opencode | dream tok | oc tok | dream s | oc s |
| --- | --- | --- | --- | --- | --- | --- |
| django__django-14411 | ✓ | ✓ | 933126 | 544138 | 385.0 | 249.0 |
| sympy__sympy-15346 | · | ✓ | 192326 | 1667978 | 447.0 | 393.9 |
| matplotlib__matplotlib-26011 | ✓ | ✓ | 148509 | 118338 | 106.3 | 107.7 |
| scikit-learn__scikit-learn-14087 | ✓ | ✓ | 868812 | 62048 | 240.3 | 52.1 |
| pytest-dev__pytest-5692 | ✓ | ✓ | 230991 | 108431 | 98.1 | 72.0 |
| sphinx-doc__sphinx-8627 | ✓ | ✓ | 173473 | 106010 | 83.8 | 88.8 |
| astropy__astropy-6938 | ✓ | ✓ | 101905 | 95288 | 69.3 | 65.2 |
| psf__requests-3362 | · | · | 318189 | 99601 | 122.3 | 84.7 |
| pylint-dev__pylint-7114 | ✓ | · | 641763 | 63993 | 220.2 | 97.7 |
| pydata__xarray-4248 | · | ✓ | 484721 | 760315 | 178.7 | 372.7 |
| mwaskom__seaborn-2848 | ✓ | ✓ | 287862 | 62461 | 117.3 | 68.8 |
| pallets__flask-4045 | ✓ | ✓ | 257997 | 229266 | 104.5 | 61.8 |
| django__django-14534 | ✓ | ✓ | 686260 | 738837 | 320.1 | 325.9 |
| sympy__sympy-19254 | · | ✓ | 350554 | 2215568 | 136.7 | 717.4 |
| matplotlib__matplotlib-23913 | ✓ | ✓ | 422433 | 193125 | 169.2 | 165.2 |
| scikit-learn__scikit-learn-25638 | ✓ | ✓ | 786912 | 437972 | 209.2 | 214.9 |
| pytest-dev__pytest-7373 | ✓ | ✓ | 197929 | 49090 | 98.0 | 49.5 |
| sphinx-doc__sphinx-8506 | ✓ | · | 243610 | 74357 | 110.0 | 54.3 |
| astropy__astropy-12907 | ✓ | ✓ | 272183 | 42459 | 107.3 | 48.6 |
| psf__requests-2317 | · | · | 1340952 | 1978347 | 894.4 | 1044.9 |
| pylint-dev__pylint-5859 | ✓ | ✓ | 205625 | 75607 | 102.3 | 109.0 |
| pydata__xarray-4493 | ✓ | ✓ | 362447 | 193926 | 156.3 | 170.3 |
| mwaskom__seaborn-3010 | ✓ | ✓ | 278485 | 43364 | 111.4 | 60.2 |
| pallets__flask-5063 | ✓ | ✓ | 274437 | 64449 | 120.1 | 79.5 |
| django__django-16820 | · | ✓ | 2784405 | 761816 | 499.1 | 289.5 |

## Notes
- dream: plan → sprint → evaluate loop, verification = FAIL_TO_PASS pytest in the testbed env.
- opencode: `run --format json` non-interactive, same prompt + acceptance command.
- Oracle-assisted: both get the acceptance test command; test files excluded from graded patch; canonical grading re-applies the pristine test_patch.
