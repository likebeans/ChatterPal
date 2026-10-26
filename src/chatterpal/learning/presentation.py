"""Public learning responses omit authored questions reserved for delayed review."""


def public_learning_result(value):
    if isinstance(value, dict):
        return {
            key: public_learning_result(item)
            for key, item in value.items()
            if key != "transfer_questions"
        }
    if isinstance(value, list):
        return [public_learning_result(item) for item in value]
    return value
