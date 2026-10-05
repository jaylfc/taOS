### Security

- Refuse an oversized PAX/GNU helper record by its declared size before its payload is read or decompressed, independent of Python's tar read chunking, with a read-size guard as a second layer.
