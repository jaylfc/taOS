# ~/.bashrc: executed by bash(1) for non-login shells.

# If not running interactively, don't do anything
case $- in
    *i*) ;;
      *) return;;
esac

# Standard defaults
HISTCONTROL=ignoreboth
shopt -s histappend
HISTSIZE=1000
HISTFILESIZE=2000
shopt -s checkwinsize

# Prompt
PS1='\[\033[01;32m\]\u@\h\[\033[00m\]:\[\033[01;34m\]\w\[\033[00m\]\$ '

# Aliases
alias ll='ls -alF'
alias la='ls -A'
alias l='ls -CF'

# TinyAgentOS info on login
if [ -d /opt/tinyagentos ]; then
    IP=$(hostname -I 2>/dev/null | awk '{print $1}')
    echo ""
    echo "  TinyAgentOS Web GUI: http://${IP:-localhost}:6969"
    echo ""
fi
