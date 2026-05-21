#!/usr/bin/env python3
import sys
sys.path.insert(0, '/Users/kaikai/hermes_scripts')
from bilibili_dm_monitor import process_conversations
import asyncio
asyncio.run(process_conversations())
