with open('ui_server.py', 'r', encoding='utf-8') as f:
    content = f.read()
    lines = content.split('\n')

# Line 1255 (index 1254) is the broken one
broken = lines[1254]
print("BEFORE:", repr(broken))

# Fix: remove the onmouseout attribute entirely (it's cosmetic only)
# Replace with a simpler version that doesn't break JS
fixed = '    \" onmouseover=\"this.style.borderColor=\'${{p.color}}\'\">'.rstrip()
# Actually, use data attribute approach to avoid quote collision
# Simplest: just remove the onmouseout since the hover effect is optional
fixed = "    \" onmouseover=\"this.style.borderColor='${{p.color}}';\" onmouseout=\"this.style.borderColor='';\">".rstrip()
lines[1254] = fixed

print("AFTER:", repr(lines[1254]))

new_content = '\n'.join(lines)
with open('ui_server.py', 'w', encoding='utf-8') as f:
    f.write(new_content)
print("File written successfully")
