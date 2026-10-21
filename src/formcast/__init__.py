"""formcast: turn blank PDF forms into field templates using an LLM agent."""

from formcast import errors
from formcast.errors import FormcastError, OpError

__version__ = "0.1.0"

__all__ = ["FormcastError", "OpError", "errors", "__version__"]
