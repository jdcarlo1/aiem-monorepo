#!/usr/bin/env python3
"""
fix_silent_excepts.py
----------------------
Run this ONCE in Replit's shell, in the same directory as main.py:

    python fix_silent_excepts.py

What it does:
1. Backs up main.py to main.py.bak_silent_except
2. Finds every `except Exception: pass` / `except Exception as x: pass`
   block (164 found when this was built) and rewrites it to log the
   exception's type, message, and original line number instead of
   swallowing it silently.
3. Finds bare `except:` blocks whose body is NOT just `pass` (e.g. `continue`)
   and widens them to `except Exception:` so they stop also catching
   KeyboardInterrupt/SystemExit. Leaves the existing body (continue, etc)
   untouched.
4. Verifies the result compiles before overwriting main.py. If it doesn't
   compile, nothing is written and your original file is untouched.

Safe to re-run: if it finds 0 more `except Exception: pass` blocks left
to convert, it will just report that and exit.
"""
import re
import sys
import shutil
import py_compile

TARGET = "main.py"
BACKUP = "main.py.bak_silent_except"


def transform(lines):
    out = []
    i = 0
    n_logged = 0
    n_widened = 0
    while i < len(lines):
        line = lines[i]
        m = re.match(r'^(\s*)except(\s+Exception(\s+as\s+(\w+))?)?\s*:\s*(#.*)?\n?$', line)
        if m:
            indent = m.group(1)
            has_exception = m.group(2) is not None
            varname = m.group(4)
            j = i + 1
            while j < len(lines) and lines[j].strip() == '':
                j += 1
            if j < len(lines):
                body_line = lines[j]
                body_indent = re.match(r'^(\s*)', body_line).group(1)
                body_stripped = body_line.strip()
                is_single_block = len(body_indent) > len(indent)
                if is_single_block:
                    kk = j + 1
                    while kk < len(lines) and lines[kk].strip() == '':
                        kk += 1
                    block_ends_here = (
                        kk >= len(lines)
                        or len(re.match(r'^(\s*)', lines[kk]).group(1)) <= len(indent)
                    )
                else:
                    block_ends_here = False

                if is_single_block and block_ends_here and body_stripped == 'pass':
                    use_var = varname if varname else '_exc'
                    out.append(f"{indent}except Exception as {use_var}:\n")
                    out.append(
                        f'{body_indent}print(f"[silent_except:L{i+1}] '
                        f'{{type({use_var}).__name__}}: {{{use_var}}}")\n'
                    )
                    n_logged += 1
                    i = j + 1
                    continue
                elif not has_exception and body_stripped != 'pass':
                    out.append(f"{indent}except Exception:\n")
                    n_widened += 1
                    i += 1
                    continue
        out.append(line)
        i += 1
    return out, n_logged, n_widened


def main():
    try:
        with open(TARGET, "r") as f:
            lines = f.readlines()
    except FileNotFoundError:
        print(f"ERROR: {TARGET} not found in current directory. cd to your project root first.")
        sys.exit(1)

    new_lines, n_logged, n_widened = transform(lines)

    if n_logged == 0 and n_widened == 0:
        print("No matching except-pass or bare-except-with-body blocks found. Nothing to do.")
        return

    shutil.copyfile(TARGET, BACKUP)

    tmp_path = TARGET + ".tmp_fixcheck"
    with open(tmp_path, "w") as f:
        f.writelines(new_lines)

    try:
        py_compile.compile(tmp_path, doraise=True)
    except py_compile.PyCompileError as e:
        print("COMPILE FAILED after transform — original file left untouched.")
        print(e)
        import os
        os.remove(tmp_path)
        sys.exit(1)

    import os
    os.replace(tmp_path, TARGET)

    print(f"Done.")
    print(f"  Logged (except+pass -> except+log): {n_logged}")
    print(f"  Widened (bare except with real body -> except Exception): {n_widened}")
    print(f"  Backup saved to: {BACKUP}")
    print(f"  {TARGET} compiles cleanly.")


if __name__ == "__main__":
    main()
