### Fixed
- moved call_next out of try/except blocks in auth_middleware.py deployed-agent and native-agent branches; call_next now only runs after try/except when principal verification succeeds