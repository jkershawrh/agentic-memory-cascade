"""Allow running as python -m benchmarks.harness"""
import asyncio
from .harness import main

asyncio.run(main())
