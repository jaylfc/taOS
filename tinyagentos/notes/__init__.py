"""Shared notes and lists: documents a user can share with agents.

A shared document is a note (freeform entries) or a list (checkable items)
that the user can share with one or more agents. Each agent share carries a
standing instruction ("research each new idea", "critique it", "start
building"); when a new entry is added, the controller posts it to the shared
agent so the agent reacts per that instruction. The store here is the data
model; the agent-reaction wiring lives in the route layer (it reuses the
working chat-to-agent channel rather than the unbuilt A2A wake loop).
"""
