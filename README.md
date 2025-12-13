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
