"""Redact OAuth callback query strings from Uvicorn access logs."""
import logging


class OAuthQueryFilter(logging.Filter):
    def filter(self, record):
        if isinstance(record.args,tuple) and len(record.args)==5:
            args=list(record.args)
            if isinstance(args[2],str) and args[2].startswith("/api/auth/social/"):
                args[2]=args[2].split("?",1)[0]
                record.args=tuple(args)
        return True


def install():
    logger=logging.getLogger("uvicorn.access")
    if not any(isinstance(item,OAuthQueryFilter) for item in logger.filters):
        logger.addFilter(OAuthQueryFilter())
