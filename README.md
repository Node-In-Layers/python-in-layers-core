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
