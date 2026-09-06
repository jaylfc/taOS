# TinyAgentOS iOS Worker

## Option 1: iPad/iPhone as a compute worker (advanced)

iOS doesn't support background inference servers like Android's Termux.
However, there are workarounds:

### Using a-Shell or iSH
- Install a-Shell from the App Store
- It can run Python and limited C programs
- Limited compared to Termux but can run the worker heartbeat agent

### Using Shortcuts + local LLM apps
- Apps like "LLM Farm" and "MLC Chat" run models locally on iOS
- They expose a local API on the device
- A Shortcut can register the device with TinyAgentOS periodically

## Option 2: iPad as a dashboard viewer

More practically, use your iPad/iPhone as a client:
- Open http://your-server:6969 in Safari
- Add to Home Screen (PWA) for app-like experience
- Monitor agents, search memory, manage the platform

## Option 3: Future native app

A native iOS worker app using MLC-LLM framework for inference
is planned but requires Swift/Objective-C development.
