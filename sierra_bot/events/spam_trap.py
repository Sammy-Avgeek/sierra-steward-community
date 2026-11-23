import logging
from collections import OrderedDict, deque
from datetime import timedelta
from time import monotonic

import discord

from ..commands.moderation import mod_log_channel
from ..config import Settings

logger = logging.getLogger(__name__)
BURST_MESSAGES = 5
BURST_SECONDS = 15


class SpamTrap:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.recent: OrderedDict[int, float] = OrderedDict()
        self.bursts: OrderedDict[int, deque[float]] = OrderedDict()

    def trigger_reason(self, message: discord.Message, now: float) -> str | None:
        if self.settings.trap_mode == "all":
            return "Posted in a dedicated trap channel"
        if message.mention_everyone or len(message.mentions) + len(message.role_mentions) >= 6:
            return "Mass mention"
        member_id = message.author.id
        timestamps = self.bursts.setdefault(member_id, deque(maxlen=BURST_MESSAGES))
        timestamps.append(now)
        self.bursts.move_to_end(member_id)
        if len(self.bursts) > 1_000:
            self.bursts.popitem(last=False)
        if len(timestamps) == BURST_MESSAGES and now - timestamps[0] <= BURST_SECONDS:
            timestamps.clear()
            return f"{BURST_MESSAGES} messages within {BURST_SECONDS} seconds"
        return None

    async def observe(self, message: discord.Message) -> None:
        member = message.author
        guild = message.guild
        if (
            guild is None or guild.id != self.settings.guild_id
            or message.channel.id != self.settings.trap_channel_id
            or message.webhook_id is not None
            or not isinstance(member, discord.Member) or member.bot
            or member.guild_permissions.manage_messages or member.id == guild.owner_id
            or message.channel.permissions_for(member).manage_messages
        ):
            return
        now = monotonic()
        if self.recent.get(member.id, 0) > now:
            return
        reason = self.trigger_reason(message, now)
        if reason is None:
            return
        self.recent[member.id] = now + 60
        self.recent.move_to_end(member.id)
        if len(self.recent) > 1_000:
            self.recent.popitem(last=False)

        action = "Staff review"
        duration = self.settings.trap_timeout_minutes
        bot = guild.me
        if (
            duration and bot is not None and bot.guild_permissions.moderate_members
            and bot.top_role > member.top_role and not member.is_timed_out()
        ):
            try:
                await member.timeout(timedelta(minutes=duration), reason="Sierra Steward spam trap")
            except discord.HTTPException:
                logger.warning("Could not apply spam-trap timeout to member %s", member.id)
            else:
                action = f"Timed out for {duration} minutes"

        logger.warning("Spam trap triggered by member %s. %s. %s.", member.id, reason, action)
        channel = mod_log_channel(guild, self.settings)
        if channel is None:
            return
        await channel.send(
            embed=discord.Embed(
                title="Spam trap triggered",
                description=f"Member <@{member.id}> in <#{message.channel.id}>.\n{reason}. {action}.",
                url=message.jump_url,
                color=discord.Color.orange(),
            ),
            allowed_mentions=discord.AllowedMentions.none(),
        )
