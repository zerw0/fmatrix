from __future__ import annotations

from .base import CommandHandlerBase
from .router import CommandRouterMixin
from .lastfm import LastfmCommandsMixin
from .discogs import DiscogsCommandsMixin


class CommandHandler(CommandRouterMixin, LastfmCommandsMixin, DiscogsCommandsMixin, CommandHandlerBase):
    pass
