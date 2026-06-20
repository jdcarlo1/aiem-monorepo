---
name: Routes after app.run() silently 404
description: Flask routes defined after the __main__ app.run() call never register at runtime
---

## The Rule
Never define `@app.route` decorators after `if __name__ == "__main__": app.run(...)`.
`app.run()` blocks forever, so Python never reaches any code below it.

**Why:** Flask route registration happens at module import time via the `@app.route` decorator.
When main.py is run directly (`python main.py`), `app.run()` starts serving and blocks.
Any `@app.route` decorators AFTER that line are never executed → routes silently return 404.

**How to apply:** Always keep `if __name__ == "__main__":` as the absolute last thing in main.py,
AFTER all route definitions. Check periodically: `grep -n "if __name__\|@app.route" main.py | tail -20`
should show routes BEFORE the __main__ block.
