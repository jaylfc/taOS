### Fixed

- Keenable broker routes now pass validated dict instead of raw request body, fixing 500 errors on valid requests - see tinyagentos/routes/broker.py:178,190,224,228
