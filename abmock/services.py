from __future__ import annotations

import random

from django.db import transaction

from .models import Experiment
from .stats import simulate, z_test


class ExperimentError(ValueError):
    pass


@transaction.atomic
def start(exp: Experiment) -> Experiment:
    exp = Experiment.objects.select_for_update().get(pk=exp.pk)
    if exp.status != Experiment.Status.DRAFT:
        raise ExperimentError("Запустить можно только черновик.")
    exp.status = Experiment.Status.RUNNING
    exp.save(update_fields=["status"])
    return exp


@transaction.atomic
def finish(exp: Experiment, rng: random.Random | None = None) -> Experiment:
    exp = Experiment.objects.select_for_update().get(pk=exp.pk)
    if exp.status != Experiment.Status.RUNNING:
        raise ExperimentError("Завершить можно только идущий тест.")
    n = exp.n_per_group
    exp.x_a, exp.x_b = simulate(n, exp.baseline_rate, exp.true_lift, rng)
    exp.n_a = exp.n_b = n
    exp.z, exp.p_value = z_test(exp.n_a, exp.x_a, exp.n_b, exp.x_b)
    exp.status = Experiment.Status.FINISHED
    exp.save()
    return exp


@transaction.atomic
def cancel(exp: Experiment) -> Experiment:
    exp = Experiment.objects.select_for_update().get(pk=exp.pk)
    if exp.status not in (Experiment.Status.DRAFT, Experiment.Status.RUNNING):
        raise ExperimentError("Отменить можно только черновик или идущий тест.")
    exp.status = Experiment.Status.CANCELLED
    exp.save(update_fields=["status"])
    return exp
