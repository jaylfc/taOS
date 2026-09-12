import mimetypes

# Test mimetypes.guess_type for various files
test_files = [
    ('test.py', 'python file'),
    ('test.yaml', 'YAML file'),
    ('test.yml', 'YAML file'),
    ('test.md', 'Markdown file'),
    ('test.log', 'LOG file'),
    ('test.toml', 'TOML file'),
    ('test.txt', 'TXT file'),
    ('test.json', 'JSON file'),
]

for filename, desc in test_files:
    mime_type, encoding = mimetypes.guess_type(filename)
    print(f"{filename:20} ({desc:25}) -> {mime_type} (encoding: {encoding})")
