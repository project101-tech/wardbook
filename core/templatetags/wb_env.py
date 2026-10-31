from django import template

from ..environments import only_mine

register = template.Library()


@register.filter
def mine_envs(envs, user):
    """The networks (of a post / notice) that this viewer is also in, so one network never learns another's names."""
    return only_mine(list(envs), user)
