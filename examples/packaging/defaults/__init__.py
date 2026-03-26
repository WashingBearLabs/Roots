"""Default agent implementations for the sample-review process."""


async def ingest_agent(input: dict) -> dict:
    return {
        "output": {"quality": "good", "data": input.get("work_item_state", {})},
        "escalate": False,
    }


async def enrich_agent(input: dict) -> dict:
    return {
        "output": {"enriched": True},
        "escalate": False,
    }


async def review_agent(input: dict) -> dict:
    return {
        "output": {"reviewed": True},
        "escalate": False,
    }


def register_agents(roots):
    agents = [
        ("ingest_agent", ingest_agent, None, None),
        ("enrich_agent", enrich_agent, None, None),
        ("review_agent", review_agent, None, None),
    ]
    registered = []
    for name, fn, in_schema, out_schema in agents:
        roots.register_agent(name, fn, input_schema=in_schema, output_schema=out_schema)
        registered.append(name)
    return registered
