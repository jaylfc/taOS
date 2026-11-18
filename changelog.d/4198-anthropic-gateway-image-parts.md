### Fixed

- **LLM gateway: images to Anthropic models**: chat messages carrying an image no longer fail with a 400 on the Anthropic route. OpenAI `image_url` parts are converted to Anthropic image blocks (`base64` source for data URLs, with any MIME parameters dropped from the media type, `url` source otherwise); a missing, empty or non-string image URL is now a 400 instead of a 500 or an upstream error (#4198).
