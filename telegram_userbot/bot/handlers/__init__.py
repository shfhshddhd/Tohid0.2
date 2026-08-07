from telegram.ext import Application, CommandHandler
from bot.handlers.start import start_command, allcommands_command
from bot.handlers.host import build_host_handler, unhost_command
from bot.handlers.target import targetadd_command, targetremove_command


def register_all(app: Application, manager) -> None:
    app.bot_data["manager"] = manager
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("allcommands", allcommands_command))
    app.add_handler(build_host_handler())
    app.add_handler(CommandHandler("unhost", unhost_command))
    app.add_handler(CommandHandler("targetadd", targetadd_command))
    app.add_handler(CommandHandler("targetremove", targetremove_command))
