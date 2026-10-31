### Fixed
- SPA now uses `/api/chat` receipt routes (GET `/api/chat/messages/{id}/receipts`, PATCH `/api/chat/receipts`) and chat WebSocket `receipt` events; removed A2A bus stream EventSource.