"""Find brace balance issues in app.js"""
content = open("dashboard/app.js", encoding="utf-8").read()
lines = content.split("\n")
depth = 0
for i, line in enumerate(lines, 1):
    for ch in line:
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth < 0:
                print(f"Line {i}: EXTRA CLOSE -> depth={depth}")
                print(f"  {line.strip()[:100]}")
                break
    if depth == 0 and i > 10 and i < len(lines) - 1:
        print(f"Line {i}: depth=0 (IIFE closed early?) -> {line.strip()[:100]}")
print(f"Final depth: {depth}")
