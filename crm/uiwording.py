"""Terminology-only migration helpers, never used as user-facing labels."""
import re

# Historical terms remain here solely to recognize previously signed documents.
_OLD_TO_NEW={
    'сотрудник':'участник команды','сотрудника':'участника команды',
    'сотруднику':'участнику команды','сотрудником':'участником команды',
    'сотрудники':'участники команды','сотрудников':'участников команды',
    'сотрудникам':'участникам команды','сотрудниками':'участниками команды',
}


def legacy_template(text):
    """Reconstruct the prior terminology for exact old-signature lookup only."""
    for old,new in sorted(_OLD_TO_NEW.items(),key=lambda item:len(item[1]),reverse=True):
        text=text.replace(new,old).replace(new[0].upper()+new[1:],old[0].upper()+old[1:])
    return text
