"""Allowlisted settings for the application's private Ollama child only."""

RUNTIME_PROFILES = ('default', 'vulkan-safe')


def runtime_environment(base, profile='default'):
    if profile not in RUNTIME_PROFILES:
        raise ValueError('Unknown managed runtime profile')
    environment = dict(base)
    if profile == 'vulkan-safe':
        environment.update(OLLAMA_VULKAN='true',
                           OLLAMA_FLASH_ATTENTION='false', OLLAMA_NUM_PARALLEL='1')
    return environment
