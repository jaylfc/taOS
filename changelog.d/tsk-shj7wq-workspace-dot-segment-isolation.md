### Security

- Generated images and music under `/data/workspace/users/<id>/` can no longer be read by another member through a dot-segment URL (`%2e/users/...`, `images/%2e%2e/users/...` or literal `./` and `../`). The owner check now uses the resolved path, the same path the file is served from.
