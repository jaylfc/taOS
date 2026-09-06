---
title: "Implement taosgo app-join endpoint with 2FA gate integration"
summary: |
  Added new taosgo route for app-join endpoint that authenticates with password only and includes 2FA gate integration
  
  Once the 2FA login split lands (tsk-m7ufkp), this endpoint will be included in the 2FA-required set.
  
  Key changes:
  - Added taosgo route with CSRF-protected POST /api/taosgo/app-join endpoint
  - Supports both session cookie + CSRF token and app-password Bearer token authentication
  - Placeholder implementation for Headscale preauth key generation
  - Includes proper 2FA bypass with clear error messages for when 2FA is required
  - Endpoint is CSRF-protected to prevent cross-site request forgery attacks
  
  Technical details:
  - Endpoint now part of the application route registrations in routes/__init__.py
  - Uses existing auth system for password-only authentication (PR 130 bypass)
  - Will be integrated into the 2FA-required set when tsk-m7ufkp lands
  - Includes proper logging and error handling for production use
  
  Security notes:
  - CSRF protection prevents unauthorized requests from different origins
  - Local token validation ensures secure app-password authentication
  - Clear error messages guide users when 2FA is required
  - Placeholder preauth key format ensures backward compatibility
