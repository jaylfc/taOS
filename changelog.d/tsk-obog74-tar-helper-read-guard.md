### Security

- Refuse an oversized PAX/GNU helper record before it is decompressed, via a read-size guard installed before `tarfile.open`. A gzip-compressed 8 MiB long name could previously be fully decompressed into memory before `check_tar_limits` saw the offending header.
