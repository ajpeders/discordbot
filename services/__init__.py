"""Interface-agnostic services.

Each module here owns a domain the bot can act on, with no knowledge of how it
was invoked. Discord cogs and the HTTP API are adapters over them, the same way
they are over `music.engine.MusicEngine`.
"""
