import asyncio, ui_server

async def main():
    resp = await ui_server.get_dashboard()
    html = resp.body.decode('utf-8')
    start = html.find('<script>') + 8
    end = html.rfind('</script>')
    js = html[start:end]
    
    with open('extracted_js.js', 'w', encoding='utf-8') as f:
        f.write(js)
    
    lines = js.split('\n')
    print(f"Total JS lines: {len(lines)}")
    for i, line in enumerate(lines, start=1):
        print(f"{i}: {repr(line[:160])}")

asyncio.run(main())
