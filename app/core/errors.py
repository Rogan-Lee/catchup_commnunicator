class AppError(Exception):
    """Base class for application errors."""


class LLMError(AppError):
    """LLM extraction failure."""


class JiraError(AppError):
    """Jira API failure."""


class SlackVerificationError(AppError):
    """Slack request signature verification failure."""


class NotFoundError(AppError):
    """Requested entity not found."""
