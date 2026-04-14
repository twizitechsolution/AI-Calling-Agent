with open('ui_server.py', 'r', encoding='utf-8') as f:
    content = f.read()

lines = content.split('\n')

with open('check_lines.txt', 'w', encoding='utf-8') as out:
    for i in range(1248, 1263):
        out.write(f"{i+1}| {lines[i]}\n")

print("Written to check_lines.txt")
