#!/usr/bin/env python3
"""Test tinycss2 token creation and serialization."""

import tinycss2
import tinycss2.ast as tinycss2_ast

print("=== Testing tinycss2 token operations ===")

# Create a simple CSS stylesheet
css = '''
@import "https://evil.example/x.css";
.test {
    background: url("/bg.png");
}
'''

print(f"Original CSS: {css}")

# Parse the CSS
tokens = tinycss2.parse_stylesheet(css, skip_comments=True, skip_whitespace=True)

print(f"\nParsed {len(tokens)} tokens")

# Display original tokens
print("\nOriginal tokens:")
for i, token in enumerate(tokens):
    print(f"  Token {i}: {type(token).__name__}")
    if token.type == 'at-rule':
        print(f"    @rule: {token.lower_at_keyword}")
        print(f"    Prelude: {token.prelude}")
    elif token.type == 'qualified-rule':
        print(f"    Rule: .test")
        print(f"    Content: {token.content}")

print("\n=== Creating new tokens with rewritten URLs ===")

# Simulate rewriting URLs
def rewrite_url(url: str) -> str:
    return f"/api/proxy?url={url}"

# Process tokens
new_tokens = []

for token in tokens:
    if token.type == "error":
        continue
        
    # Handle @import at-rules
    if token.type == "at-rule" and token.lower_at_keyword == "import":
        new_prelude = []
        for p in token.prelude:
            if p.type == "string":
                # Create new string token with rewritten URL
                new_prelude.append(tinycss2_ast.StringToken(
                    rewrite_url(p.value),
                    p.source_line,
                    p.source_column,
                    p.representation,
                ))
            else:
                new_prelude.append(p)
        
        # Create new at-rule
        new_token = tinycss2_ast.AtRule(
            token.at_keyword,
            token.content,
            token.lower_at_keyword,
            new_prelude,
        )
        new_tokens.append(new_token)
        
    # Handle url() functions
    elif token.type == "qualified-rule":
        # Check if the rule contains a url() function
        for content_token in token.content:
            if hasattr(content_token, 'type') and content_token.type == 'function':
                if content_token.lower_name == 'url':
                    # Create new function with rewritten URL
                    if content_token.arguments and len(content_token.arguments) > 0:
                        arg = content_token.arguments[0]
                        if arg.type == "string":
                            new_arg = tinycss2_ast.StringToken(
                                rewrite_url(arg.value),
                                arg.source_line,
                                arg.source_column,
                                arg.representation,
                            )
                            # Note: Creating a new FunctionBlock is complex
                            # For now, we'll just show the concept
                            print(f"  Would rewrite url() with value: {arg.value}")
                            print(f"    To: {rewrite_url(arg.value)}")
    
        new_tokens.append(token)

print(f"\nProcessed {len(new_tokens)} tokens")

print("\n=== Testing serializer ===")
print(f"tinycss2.serializer: {tinycss2.serializer}")
print(f"tinycss2.serializer.serialize_to: {tinycss2.serializer.serialize_to}")

# Test serialization
output = []
for token in tokens:
    tinycss2.serializer.serialize_to([token], output.append)

print(f"Serialized output: {''.join(output)}")
EOF