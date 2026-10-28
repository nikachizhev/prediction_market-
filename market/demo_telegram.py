"""No-network transport used exclusively by observability demo settings."""
import logging

log = logging.getLogger(__name__)


def ask_creator(question):
    log.info('Demo: ask creator for market %s', question.pk)


def remind_creator(question, number):
    log.info('Demo: reminder %s for market %s', number, question.pk)


def send_results(question):
    log.info('Demo: result for market %s', question.pk)
