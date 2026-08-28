# In Layers Core


Python port of the Node-in-Layers core framework.
Supports  Domains, config and layers loading, and cross-layer logging.

Key points:
- Domains explicitly provided in config (no convention discovery)
- Layers are loaded in configured order (supports composite layers)
- Cross-layer logging with automatic id propagation and function wraps

# Pecularities, Limitations, and Recommendations

## No Keyword Arguments for Layer level Functions
For the public functions for a given layer, the arguments cannot use kwargs.
The reason behind this is it creates a consistent interface to allow the framework and other tools to work.

We recommend making arguments an object (class instance, dict), and making the last argument a "cross_layer_props" object, that can pass along across layers.

## OpenTelemetry Support

### What it is
`in_layers.core` can now expose OpenTelemetry traces, metrics, and structured log records during the normal layer-function wrapping that the framework already performs. This keeps Python ergonomics intact while bringing the same high-value observability hooks that exist in Node.

### How to use it
Enable the OTEL signal(s) you want under `in_layers_core.logging.otel`. The runtime lazily imports OTEL packages, so projects that do not install them can still use the framework without failing at import time.

```python
from box import Box
from in_layers.core import load_system, SystemProps, LogFormat, LogLevelNames

config = Box(
    system_name="billing-api",
    environment="prod",
    in_layers_core=Box(
        logging=Box(
            log_level=LogLevelNames.info,
            log_format=[LogFormat.json, LogFormat.otel],
            otel=Box(
                service_name="billing-api",
                version="2026.08.28",
                forward_baggage=True,
                trace=Box(enabled=True),
                metrics=Box(enabled=True),
                logs=Box(enabled=True),
            ),
        ),
        layer_order=["services", "features"],
        domains=[...],
    ),
)

system = load_system(SystemProps(environment="prod", config=config))
```

### Expected behavior
Layer calls emit spans and duration/call counters automatically. If OTEL logging is enabled, framework log messages are also mirrored into the OTEL log pipeline with ids and context flattened into attributes.

### Difference from Node
The Python version intentionally stays synchronous for now and uses lazy imports plus no-op fallbacks instead of requiring OTEL packages up front.

## Cross-Layer Props Helpers

### What it is
Cross-layer props now support richer logging and trace context, including one-hop logging overrides and OTEL baggage forwarding.

### How to use it
Use the helpers when you need to add request metadata, suppress payload logging for one call, or attach OTEL baggage before crossing into another layer.

```python
from in_layers.core import (
    create_cross_layer_props,
    cross_layer_props_with_logging_overrides,
    cross_layer_props_with_otel_baggage,
)


def run_checkout(layer_logger, incoming_cross_layer_props):
    cross = create_cross_layer_props(layer_logger, incoming_cross_layer_props)
    # Useful for reducing log payload size, for the subsequent layer calls.
    cross = cross_layer_props_with_logging_overrides(
        {"omit_data": True},
        cross,
    )
    cross = cross_layer_props_with_otel_baggage(
        {"tenant_id": "tenant-42", "checkout_id": "chk-1001"},
        cross,
    )
    return cross
```

### Expected behavior
`create_cross_layer_props()` appends framework ids, strips one-hop overrides from the payload that gets forwarded, and optionally carries OTEL baggage when `forward_baggage` is enabled in config.

### Difference from Node
The semantics match the Node runtime, but the Python surface is deliberately plain-data and Pydantic-friendly rather than relying on functional-models helpers.

## Function Logger Block Wrapping

### What it is
`get_inner_logger()` returns the normal logger scope to use inside a layer function. That returned logger can wrap arbitrary blocks inside the function, not just the outer layer call. Use `wrap_step()` for a wrapped block/span within the current function, and `wrap_function_call()` when you want to pass a named function-style logger into a helper.

### How to use it

```python
def persist_invoice(ctx, payload, cross_layer_props=None):
    return ctx.services.billing.save_invoice(
        payload,
        cross_layer_props=cross_layer_props,
    )


class InvoiceFeatures:
    def __init__(self, ctx):
        self._ctx = ctx

    def reconcile(self, payload, cross_layer_props=None):
        log = self._ctx.log.get_inner_logger("reconcile", cross_layer_props)
        return log.wrap_step(
            lambda: persist_invoice(self._ctx, payload, cross_layer_props),
            {
                "args": [payload],
            },
        )

    def sync_with_helper(self, payload, cross_layer_props=None):
        log = self._ctx.log.get_inner_logger("sync_with_helper", cross_layer_props)
        return log.wrap_function_call(
            "persist_invoice",
            lambda helper_log, invoice_payload: helper_log.wrap_step(
                lambda: persist_invoice(
                    self._ctx,
                    invoice_payload,
                    cross_layer_props,
                ),
                {"args": [invoice_payload]},
            ),
            {"args": [payload]},
        )
```

### Expected behavior
The wrapped block gets the same start/end/error wrap logging as normal layer methods, including OTEL spans and metrics when configured. `wrap_step()` stays in the current function scope, while `wrap_function_call()` creates a nested function-style scope with its own `function_call_id`. If the passed `cross_layer_props` contains `logging.overrides.omit_data=True`, the wrapper suppresses args/results for that one hop.

## Layer Visibility and Late-Bound Getters

### What it is
Loaded layers now get late-bound lookup helpers so a function can access finalized services or features for another domain without depending on unfinished load-time state.

### How to use it

```python
class OrdersFeatures:
    def __init__(self, ctx):
        self._ctx = ctx

    def create_order(self, payload, cross_layer_props=None):
        payments = self._ctx.services.get_services("payments")
        notifications = self._ctx.features.get_features("notifications")

        charge = payments.charge(payload, cross_layer_props=cross_layer_props)
        notifications.send_order_confirmation(
            {"order_id": charge["order_id"]},
            cross_layer_props=cross_layer_props,
        )
        return charge
```

### Expected behavior
`services.get_services()` is available during loaded layer execution, and `features.get_features()` is available only where the configured layer ordering says features are finalized and visible.

## Models, CRUD Factories, and Wrapper Control

### What it is
Model CRUD exposure is now more configurable. You can enable CRUD wrappers broadly, override how CRUD wrappers are built, and opt out of wrapping those CRUD functions with framework logging when needed.

### How to use it

```python
from box import Box
from in_layers.core import create_model_cruds


def create_customer_cruds(model, _context, options=None):
    return create_model_cruds(
        model,
        {
            "overrides": {
                "create": lambda data=None, **kwargs: model.create(data, **kwargs).to_pydantic(),
            }
        },
    )


config = Box(
    system_name="crm",
    environment="test",
    in_layers_core=Box(
        logging=Box(...),
        layer_order=["services", "features"],
        domains=[...],
        models=Box(
            model_backend="persistence",
            model_services_cruds=True,
            model_features_cruds=True,
        ),
        model_cruds_factory=[
            {
                "layer": "services",
                "domain": "customers",
                "model": "Customers",
                "factory": create_customer_cruds,
            }
        ],
        no_model_log_wrap=False,
    ),
)
```

### Expected behavior
CRUD wrappers are exposed without colliding with `Box` method names like `update`, and factory overrides can target specific layer/domain/model combinations. Setting `no_model_log_wrap=True` leaves those CRUD callables unwrapped.

## Annotated Functions

### What it is
`annotated_function()` attaches schema and metadata to a Python callable so it can be used for MCP-style tools, docs, or runtime validation while staying idiomatic to Python.

### How to use it

```python
from in_layers.core import AnnotatedFunctionProps, annotated_function, annotation_function_props


lookup_customer = annotated_function(
    annotation_function_props(
        AnnotatedFunctionProps(
            function_name="lookup_customer",
            domain="crm",
            args_schema=dict[str, str],
            returns_schema=dict[str, str],
            description="Look up a customer record by id.",
        )
    ),
    lambda args, cross_layer_props=None: {
        "customer_id": args["customer_id"],
        "status": "active",
    },
)

result = lookup_customer({"customer_id": "cus-123"})
schema = lookup_customer.schema
```

### Expected behavior
The wrapper validates the first argument against the declared input schema, validates non-error return values when a return schema is present, and exposes metadata like `function_name`, `domain`, `description`, and `schema`.

## Contributing

### Running Unit Tests
```bash
poetry run pytest --cov=. --cov-report=term-missing --cov-report=html -q
```

### Auto-Cleaning / Checking Tools
```bash
./bin/lint.sh
```

### Publishing
```bash
./bin/deploy.sh
```

## Models and Persistence Backends

### Overview
- Models are standard Pydantic classes decorated with `@model(domain=..., plural_name=...)`.
- When a domain’s `services` layer is loaded, the framework discovers the domain’s models and exposes them as SimpleModel wrappers under:
  - `context.models.<domain>.get_models() -> Box`, keyed by the model’s plural name
  - Example access: `context.models.mydomain.get_models().MyModels`
- Each entry in this mapping is a SimpleModel wrapper with:
  - `instance(data | **kwargs)` to wrap raw data
  - `create(data | **kwargs)` to persist through a backend
  - `retrieve(id)`, `update(id, **kwargs)`, `delete(id)`, `search(query)`
  - `get_model_definition()`, `get_primary_key_name()`, `get_primary_key(data)`
- A SimpleModel instance supports zero-arg getters for its data via `instance.get.<field>()`.

Important: Persistence uses a backend returned by a model backend provider living in the services of the domain named by `in_layers_core.models.model_backend`. If none is provided, a core fallback uses a no-op backend (CRUD operations will raise NotImplemented).

### Declaring a Model
```python
from pydantic import BaseModel, Field
from in_layers.core.models.libs import model

@model(domain="pipeline", plural_name="PipelineJobs")
class PipelineJob(BaseModel):
    id: str = Field(...)
    name: str = Field(...)
```

### Providing a Model Backend (via a Domain’s Services)
You must provide, in the configured domain’s services layer, a method that returns a backend for each model. The service needs a method:
- `get_model_backend(model_definition) -> BackendProtocol`

For example:

```python
# services.py

class MyDomainServices:
    def __init__(self, ctx):
        self._ctx = ctx

    def get_model_backend(self, model_definition):
        # you can check the domain, the name of the model (if it is model/domain specific)
        # return your BackendProtocol implementation (e.g., Mongo, SQL, etc.)
        return MyConcreteBackend(...)


Then tell the framework which backend provider to use via config:
```python
config = Box(
    system_name="test",
    environment="test",
    in_layers_core=Box(
        logging=Box(...),
        layer_order=["services", "features"],
        domains=[...],
        models=Box(
            # Choose your model backend by telling the framework which domain it lives in.
            # The framework will call mydomain.services.get_model_backend(model_definition)
            model_backend="mydomain",
            # Optional: surface CRUD wrappers in services/features
            model_services_cruds=True,
            model_features_cruds=False,
        ),
    ),
)
```

Notes:
- Ensure the configured domain’s services are loaded before domains whose models you want to wrap (via domain ordering and `layer_order`).
- If not provided, the framework falls back to a core default provider, which uses a no-op backend (CRUD is not implemented).
- If `model_features_cruds` is true, `model_services_cruds` is implicitly treated as true and both layers expose `cruds.<Plural>` wrappers.

### Using Models in Services
```python
from pydantic import BaseModel
from in_layers.core.models.libs import model
# ./mydomain/models.py
@model(domain="mydomain", plural_name="MyModels")
class MyModel(BaseModel):
    id: str
    name: str
```

```python
# ./mydomain/services.py
from types import SimpleNamespace

class MyServices:
    def __init__(self, ctx):
        self._ctx = ctx

    def return_a_model_instance(self):
        models = self._ctx.models.mydomain.get_models()
        MyModels = models.MyModels
        # Create a non-persisted instance via kwargs (or Mapping)
        inst = MyModels.instance(id="123", name="John Doe")
        # Access fields
        assert inst.get.id() == "123"
        assert inst.get.name() == "John Doe"
        return inst
```

```python
# ./mydomain/__init__.py
from . import services, models
name = "mydomain"
__all__ = ['name', 'services', 'models']
```

### Backends
Backends implement `BackendProtocol`:
- `create(model, data) -> Mapping`
- `retrieve(model, id) -> Mapping | None`
- `update(model, id, data) -> Mapping`
- `delete(model, id) -> None`
- `search(model, query) -> ModelSearchResult`

Your persistence factory decides which backend to return per model class (e.g., route different models to different datastores).

### Instance Creation Options
- Mapping:
  - `MyModels.instance({"id": "123", "name": "John"})`
  - `MyModels.create({"id": "123", "name": "John"})`
- Keywords:
  - `MyModels.instance(id="123", name="John")`
  - `MyModels.create(id="123", name="John")`

When both are provided, keyword arguments override keys in the mapping.
