## Writing

- Omit needless words
- When you reply to me in chat conversations, make your replies easier to scan, e.g. add white space, use simple and clear sentences.
- Write everything in plain language: chat replies, notebooks, docstrings, comments, commit messages.
  Short sentences. Everyday words over technical ones where both are accurate, e.g. "how often" not "the rate at which", "the same" not "identical".
  Keep the precise term where it is the real name for the thing: a missing value is a "null", not a "blank".
  Cut hedges and filler: "essentially", "effectively", "it is worth noting that", "and nothing else", "the cost is real".
- Prefer a heading that asks the question the section answers, e.g. "Are the nulls closures?" over "Null diagnosis".
- Explain a method by what it does concretely before naming it.
  "Take every Monday 08:00 in the two years and ask how often it took money" beats "pool by weekday half-hour".
- Do not use a term of art where a sentence would do.
  If a term genuinely earns its place, define it once in plain words at first use.
- When writing propose, follow PROSE.md
- Never use the em dash "—". Use plain dash "-" instead.
- Do not write README.md or similar unless instructed so.
- When writing or substantially editing long Markdown files, put each full sentence on its own line.
  Preserve normal Markdown structure, but avoid wrapping multiple sentences onto one physical line.


## Planning in plan mode

- When I run plan mode, I want you to interview me about the assignment or spec document we must plan on how to implement.
- Ask ONE question at a time. Wait for my answer before the next.
- Ask only about the calls you cannot make alone: which of two approaches, where
  a boundary goes, what to build now versus later.
- Put the alternatives in the question, with the tradeoff. Not "what do you
  want", but "A costs this, B costs that, which one".
- If I give a vague answer, push once for a concrete one.
- Stop asking once no further answer would change the plan.
- Always include the plan directly in the code directory
- In the plan, add a section called Tasks where you break down the high-level tasks to execute.
Then after the plan is approved and you work on each task, add a consise summary in the plan of how the task execution went and then commit and push the code changes to the remote repo.


## Software Development Life Cycle Rules

- When making technical decisions, do not give much weight to development cost.
  Instead, prefer quality, simplicity, robustness, scalability, and long term maintainability.
- If something in my assignment is unlcear, err on the side of asking me a lot of questions.
- Use clean architecture, dependency inversion and dependency injection as much as possible.
- Always consider and comminicate about the scalability and performance of the architecture, but follow the principle to build first and optimize later.
- Start with simple approaches and not with unnecessary abstractions.
  If in doubt, ask me.
- Use red/green TDD development style
- By default, containerize the software in Docker containers
- By default, you (claude code) are running and developing in a docker sandbox (sbx) so make sure you to take this into account
- When writing tests, prefer fakes instead of mocks. This is enabled by using clean architecture in the first place.
- Write a couple of simple tests per task that catch the vast majority of problems.
  Avoid exhaustive parametrized suites, helper indirection, and tests that re-verify what a library already guarantees.
  Pick the assertions that would fail if the code were broken in a realistic way: one happy path with real values, one representative case per distinct failure mode, then stop.
- Tests must fail when expected behavior is not met; never add conditional logic, fallback branches, or exception handling that can mask failures or allow incorrect behavior to pass.
- Apply that same high standard to engineering excellence: lint, test failures, and test flakiness.
  If you see one, even if it is not caused by what you are working on right now, still get it fixed.
- When end-to-end testing a product, be picky about the UI you see and be obsessed with pixel perfection.
  If something clearly looks off, even if it is not directly related to what you are doing, try to get it fixed along the way.
- When doing bug fixes, always start with reproducing the bug in an E2E setting as closely aligned with how an end user will experience it as possible.
This makes sure you find the real problem so your fix will actually solve it.


## Code Style

- In the code you write, add comments (starting with a small letter) that explain the why behind the code (not the how).
- Avoid docstrings for small, internal implementation helpers; instead, use comments to explain the "why".
  For public APIs, libraries, and modules, do use clear docstrings following standard Python conventions so external contracts remain well documented.
- Do not remove comments from the codebase.
- Avoid many nested for loops or if-else statements.
- Use descriptive variable names, e.g. prefer "buckets" over "b"
- Avoid global constants
- Use defensive programming where you validate function parameter immediately after the function signature
- Do not use default values for function parameters, except `None`. If a non-`None` default seems warranted, ask the user before adding it.
- When calling functions, use keyword arguments explicitly, e.g. do foobar(a=1) instead of foobar(1).

### Python-specific rules

- Use uv for dependency management, ruff for linting, ty for typing, pytest for testing
- Use modern Python syntax for Python >=3.11, depending on the version created by uv.
- For Python typing, use `| None` union instead of `Optional`.
- For Python typing, use the standard library as much as possible instead of importing `from typing ...`.

### Machine Learning & Data Science

- Whenever ML code produces a model, evaluation, dataset split, prediction set, or other experimental artifact, make the result reproducible by default.
  For example, explicitly seed all sources of randomness, ensure environment and hardware equivalence between runs, set all configurations to a single input file.
- Save models, metrics, predictions, and other outputs as explicit artifacts.
- Do not rely on notebook state, execution order, or manually configured environment state.
- Document any unavoidable nondeterminism.
- The quality of the data is at least as important as the code quality.
- Avoid data leakage rigorously (for example, look-ahead bias) when doing exploratory data analysis, doing a train-validation-test dataset split, or evaluation the trained mode on the test dataset.
- Never build ML modules guided by the values in a specific dataset, instead write generalizable algorithms.
  As an example, do not look at the data to decide which ML models performs best in your own hidden coding sessions, instead write for me the code which does the model selection explicitly and visibly to me.
- Spell tensor axis names out in full, in both the type annotations and the einops patterns, and keep the two in agreement: `batch_size`, `sequence_length`, `d_model`, `num_heads`, `in_features`.
  Never single letters or short forms such as `B`, `T`, `L`, `N`, or `b s d`.
  Single letters collide across the literature: `N` is the batch in some papers and the sequence in others, `L` is the sequence length in most papers but the layer count in scaling-law papers, and `T` is the sequence in model code but the temperature in decoding code.
  Where a module takes arbitrary leading dimensions, use `...` followed by the named trailing axes, for example `Float[Tensor, " ... in_features"]`.


## Git

- use 'main' instead of 'master' for the main branch name
- NEVER include your agent name (for exmaple "By Claude") in commit messages or PR descriptions.
- Before staging code with `git add`, always run the linter and tests first.
  Fix any errors before proceeding. Only stage files after both pass successfully.
- Start your commit messages with a small letter.
- Do not reference Linear tickets explicitly (in commit messages, PR descriptions, or code comments); assume the reader may not have access to Linear, so summarize the relevant context inline instead.
- Never manually modify CHANGELOG.md files or any files that are marked as auto-generated.
