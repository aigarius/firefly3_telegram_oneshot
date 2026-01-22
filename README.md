# firefly3_telegram_oneshot

One shot Telegram bot for adding cash transactions to Firefly3 finance app

Use the provided Dockerfile or a built image on Docker Hub. See Dockerfile
for the arguments that need to be provided as environment variables.

You need a the URL and Personal Access Token of your Firefly III instance.
See https://docs.firefly-iii.org/how-to/firefly-iii/features/api/#personal-access-token

For Telegram you need to create a new bot and get a new bot token:
https://core.telegram.org/bots/tutorial

You will also need to figure out the Telegram id of your user. To do that
talk to https://telegram.me/userinfobot and say "/start".

Once the variables are set and the Docker container is running, talk to your
new bot and say "/start" once to check the connection. The following actions work:

"/help" - shows simple help

"/last" - displays last transaction in the configured cash account

"/undo" - marks the transaction that /last displays as deleted

"/cat one or more key words" - searches a category that best matches the keywords

"/dest one or more key words" - same for destination accounts

"23.12 Coffe, milk, sugar, cat=Food, dest=Edeka" - creates a new cash transaction

The text from "cat=" or "dest=" to the next comma is used to find the category
or destination account (same as /cat and /dest commands). You can create a new
category or destination account by prefixing the name with "+" (e.g. "cat=+Food").
Default destination account is "Unknown" and default category is no category.

The one-shot nature of the message allows them to be sent when the user is actually
offline and have the server process the messages later, when they actually arrive
to the Telegram bot.
