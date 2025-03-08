"""Resource template functionality."""

import inspect
import re
from typing import TYPE_CHECKING, Any, Callable

from pydantic import BaseModel, Field, validate_call

from mcp.server.fastmcp.resources.types import FunctionResource, Resource
from mcp.server.fastmcp.utilities.func_metadata import func_metadata

if TYPE_CHECKING:
    from mcp.server.fastmcp.server import Context


class ResourceTemplate(BaseModel):
    """A template for dynamically creating resources."""

    uri_template: str = Field(
        description="URI template with parameters (e.g. weather://{city}/current)"
    )
    name: str = Field(description="Name of the resource")
    description: str | None = Field(description="Description of what the resource does")
    mime_type: str = Field(
        default="text/plain", description="MIME type of the resource content"
    )
    fn: Callable = Field(exclude=True)
    parameters: dict = Field(description="JSON schema for function parameters")
    context_kwarg: str | None = Field(
        None, description="Name of the kwarg that should receive context"
    )

    @classmethod
    def from_function(
        cls,
        fn: Callable,
        uri_template: str,
        name: str | None = None,
        description: str | None = None,
        mime_type: str | None = None,
    ) -> "ResourceTemplate":
        """Create a template from a function."""
        func_name = name or fn.__name__
        if func_name == "<lambda>":
            raise ValueError("You must provide a name for lambda functions")

        # Find context parameter if it exists
        context_kwarg = None
        sig = inspect.signature(fn)
        for param_name, param in sig.parameters.items():
            if (
                param.annotation.__name__ == "Context"
                if hasattr(param.annotation, "__name__")
                else False
            ):
                context_kwarg = param_name
                break

        # Get schema from func_metadata, excluding context parameter
        func_arg_metadata = func_metadata(
            fn,
            skip_names=[context_kwarg] if context_kwarg is not None else [],
        )
        parameters = func_arg_metadata.arg_model.model_json_schema()

        # ensure the arguments are properly cast
        fn = validate_call(fn)

        return cls(
            uri_template=uri_template,
            name=func_name,
            description=description or fn.__doc__ or "",
            mime_type=mime_type or "text/plain",
            fn=fn,
            parameters=parameters,
            context_kwarg=context_kwarg,
        )

    def matches(self, uri: str) -> dict[str, Any] | None:
        """Check if URI matches template and extract parameters."""
        # Convert template to regex pattern
        pattern = self.uri_template.replace("{", "(?P<").replace("}", ">[^/]+)")
        match = re.match(f"^{pattern}$", uri)
        if match:
            return match.groupdict()
        return None

    async def create_resource(
        self, uri: str, params: dict[str, Any], context: "Context | None" = None
    ) -> Resource:
        """Create a resource from the template with the given parameters."""
        try:
            # Add context to params if needed
            if self.context_kwarg is not None and context is not None:
                params = {**params, self.context_kwarg: context}

            # Call function and check if result is a coroutine
            result = self.fn(**params)
            if inspect.iscoroutine(result):
                result = await result

            return FunctionResource(
                uri=uri,  # type: ignore
                name=self.name,
                description=self.description,
                mime_type=self.mime_type,
                fn=lambda: result,  # Capture result in closure
            )
        except Exception as e:
            raise ValueError(f"Error creating resource from template: {e}")
