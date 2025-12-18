from pydantic import BaseModel

from in_layers.core.models.libs import model


@model(domain="test", plural_name="MyModels")
class MyModel(BaseModel):
    pass
