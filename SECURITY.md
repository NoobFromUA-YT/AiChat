# Security

AI Chat (aichat-desktop) runs the `claude` command with the permissions you choose (Read only by default; Full auto can run any command).
Please report security problems privately through GitHub's "Report a vulnerability" button on the Security tab of this
repository instead of a public issue.

What is stored where: the API key is kept in plain text with owner-only permissions in the aichat-desktop config folder, chats are
stored unencrypted next to it (see "Security notes" in the README). Never paste a key into an issue.
