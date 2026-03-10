"""Telegram bot setup for AI Mesh — single bot with long-polling."""

import structlog

from aimesh.telegram.handlers import CommandHandlers
from aimesh.telegram.setup_wizard import SetupWizard

logger = structlog.get_logger("bot")


class TelegramBot:
    """Wraps python-telegram-bot for AI Mesh.

    Uses a single bot token with persona prefixes for agent identity.
    Long-polling for v1 (webhook in future).
    """

    def __init__(
        self,
        token: str,
        handlers: CommandHandlers,
        setup_wizard: SetupWizard | None = None,
        nl_handler=None,
    ) -> None:
        self.token = token
        self.handlers = handlers
        self.setup_wizard = setup_wizard
        self.nl_handler = nl_handler
        self._app = None

    async def setup(self) -> None:
        """Initialize the telegram bot application."""
        from telegram.ext import ApplicationBuilder, CommandHandler

        self._app = ApplicationBuilder().token(self.token).build()

        # Register setup wizard (ConversationHandler must be added before plain CommandHandlers)
        if self.setup_wizard is not None:
            self._app.add_handler(self.setup_wizard.get_handler())

        # Register command handlers
        self._app.add_handler(CommandHandler("task", self._on_task))
        self._app.add_handler(CommandHandler("status", self._on_status))
        self._app.add_handler(CommandHandler("agents", self._on_agents))
        self._app.add_handler(CommandHandler("cancel", self._on_cancel))
        self._app.add_handler(CommandHandler("approve", self._on_approve))
        self._app.add_handler(CommandHandler("reject", self._on_reject))

        # Register NL handler (lower priority than command handlers)
        if self.nl_handler is not None:
            from telegram.ext import MessageHandler, filters

            self._app.add_handler(MessageHandler(
                filters.TEXT & ~filters.COMMAND,
                self.nl_handler.handle_message,
            ))

        logger.info("bot_setup_complete")

    async def start(self) -> None:
        """Start the bot with long-polling."""
        if self._app:
            await self._app.initialize()
            await self._app.start()
            await self._app.updater.start_polling()
            logger.info("bot_started")

    async def stop(self) -> None:
        """Stop the bot gracefully."""
        if self._app:
            await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()
            logger.info("bot_stopped")

    def get_send_fn(self):
        """Return an async send function for the display layer."""
        async def send(chat_id: int, text: str, parse_mode: str = "MarkdownV2"):
            if self._app and self._app.bot:
                await self._app.bot.send_message(
                    chat_id=chat_id, text=text, parse_mode=parse_mode
                )
        return send

    async def _on_task(self, update, context) -> None:
        text = " ".join(context.args) if context.args else ""
        response = await self.handlers.handle_task(update.effective_user.id, text)
        await update.message.reply_text(response, parse_mode="MarkdownV2")

    async def _on_status(self, update, context) -> None:
        response = await self.handlers.handle_status(update.effective_user.id)
        await update.message.reply_text(response, parse_mode="MarkdownV2")

    async def _on_agents(self, update, context) -> None:
        response = await self.handlers.handle_agents(update.effective_user.id)
        await update.message.reply_text(response, parse_mode="MarkdownV2")

    async def _on_cancel(self, update, context) -> None:
        task_id = context.args[0] if context.args else ""
        response = await self.handlers.handle_cancel(update.effective_user.id, task_id)
        await update.message.reply_text(response, parse_mode="MarkdownV2")

    async def _on_approve(self, update, context) -> None:
        task_id = context.args[0] if context.args else ""
        response = await self.handlers.handle_approve(update.effective_user.id, task_id)
        await update.message.reply_text(response, parse_mode="MarkdownV2")

    async def _on_reject(self, update, context) -> None:
        task_id = context.args[0] if context.args else ""
        feedback = " ".join(context.args[1:]) if len(context.args) > 1 else ""
        response = await self.handlers.handle_reject(update.effective_user.id, task_id, feedback)
        await update.message.reply_text(response, parse_mode="MarkdownV2")
