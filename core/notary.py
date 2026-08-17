"""Deprecated compatibility alias for the JSON state adapter.

Auction rules now live in :mod:`core.auction_manager`; persistence lives in
:mod:`utils.json_store`.
"""

from utils.json_store import JsonStore

Notaio = JsonStore
