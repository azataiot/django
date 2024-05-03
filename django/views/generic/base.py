import logging

from asgiref.sync import iscoroutinefunction, markcoroutinefunction

from django.core.exceptions import ImproperlyConfigured
from django.http import (
    HttpResponse,
    HttpResponseGone,
    HttpResponseNotAllowed,
    HttpResponsePermanentRedirect,
    HttpResponseRedirect,
)
from django.template.response import TemplateResponse
from django.urls import reverse
from django.utils.decorators import classonlymethod
from django.utils.functional import classproperty

logger = logging.getLogger("django.request")


class ContextMixin:
    """
    typing.Hashable -> object -> ContextMixin
    A default context mixin that passes the keyword arguments received by
    get_context_data() as the template context.
    """

    extra_context = None

    def get_context_data(self, **kwargs):
        """
        Return the context for this view. Will always add at least the `view` attribute to the context data.
        """
        kwargs.setdefault("view", self)
        if self.extra_context is not None:
            kwargs.update(self.extra_context)
        return kwargs


class View:
    """
    Intentionally simple parent class for all views. Only implements
    dispatch-by-method and simple sanity checking.
    """

    # List of HTTP methods that the view can handle.
    # https://developer.mozilla.org/en-US/docs/Web/HTTP/Methods
    http_method_names = [
        "get",
        "post",
        "put",
        "patch",
        "delete",
        "head",
        "options",
        "trace",
    ]

    def __init__(self, **kwargs):
        """
        Constructor. Called in the URLconf; can contain helpful extra
        keyword arguments, and other things.
        """
        # Go through keyword arguments, and either save their values to our
        # instance, or raise an error.
        for key, value in kwargs.items():
            setattr(self, key, value)

    @classproperty
    def view_is_async(cls):
        """
        Checks if the HTTP handlers (methods like get, post, etc.) are all async or all sync.
        """
        # Gather all the handlers (HTTP handler methods) that the class defines
        # (like get, post, etc. but exclude options).
        # Same as:
        # handlers = []
        # for method in cls.http_method_names:
        #    if method != "options" and hasattr(cls, method):
        #        handlers.append(getattr(cls, method))
        handlers = [
            getattr(cls, method)
            for method in cls.http_method_names
            # Exclude the options method as it is not required to be defined (already implemented by default).
            if (method != "options" and hasattr(cls, method))
        ]
        if not handlers:
            return False
        # Check if the first handler is async or not.
        is_async = iscoroutinefunction(handlers[0])
        # Check if all the handlers are async or not.
        if not all(iscoroutinefunction(h) == is_async for h in handlers[1:]):
            raise ImproperlyConfigured(
                f"{cls.__qualname__} HTTP handlers must either be all sync or all "
                "async."
            )
        return is_async

    @classonlymethod
    def as_view(cls, **initkwargs):
        """
        Main entry point for a request-response process.
        Used to create an instance of the class and call the dispatch method.
        """
        for key in initkwargs:
            if key in cls.http_method_names:
                # It first checks if the key is in the list of HTTP methods (like get, post, etc.).
                # If it is, it raises an error,
                # to prevent the user from passing a method name as a keyword argument.
                raise TypeError(
                    "The method name %s is not accepted as a keyword argument "
                    "to %s()." % (key, cls.__name__)
                )
            if not hasattr(cls, key):
                # It then checks if the key is an attribute of the class.
                # If it is not, it raises an error,
                # Here, the as_view method provides a way to override the attributes of the class,
                # only if they are already defined.
                raise TypeError(
                    "%s() received an invalid keyword %r. as_view "
                    "only accepts arguments that are already "
                    "attributes of the class." % (cls.__name__, key)
                )

        # When the as_view method is called, it returns the view function.
        # When a request comes in that matches the URL pattern, Django calls the view function and
        # passes the HttpRequest object as the first argument, which is request in this case.
        def view(request, *args, **kwargs):
            self = cls(**initkwargs)
            # The setup method is called to initialize attributes shared by all view methods.
            # The request object, positional arguments, and keyword arguments are passed to the setup method.
            # The setup method can be overridden to provide custom initialization.
            self.setup(request, *args, **kwargs)
            if not hasattr(self, "request"):
                raise AttributeError(
                    "%s instance has no 'request' attribute. Did you override "
                    "setup() and forget to call super()?" % cls.__name__
                )
            return self.dispatch(request, *args, **kwargs)

        # The class that defines the view is stored in the view_class attribute of the view function.
        view.view_class = cls
        # The keyword arguments passed to the as_view method are
        # stored in the view_initkwargs attribute of the view function.
        view.view_initkwargs = initkwargs

        # __name__ and __qualname__ are intentionally left unchanged as
        # view_class should be used to robustly determine the name of the view
        # instead.
        view.__doc__ = cls.__doc__
        view.__module__ = cls.__module__
        view.__annotations__ = cls.dispatch.__annotations__
        # Copy possible attributes set by decorators, e.g. @csrf_exempt, from
        # the dispatch method.
        view.__dict__.update(cls.dispatch.__dict__)

        # Mark the callback if the view class is async.
        if cls.view_is_async:
            markcoroutinefunction(view)

        return view

    # The setup() method in Django views is called before the dispatch() method.
    # It is seperated from the __init__ method to allow for easier overriding.
    def setup(self, request, *args, **kwargs):
        """Initialize attributes shared by all view methods."""
        # Checks if the view has a get method and not a head method. In that case,
        # it sets the head method to be the same as the get method.
        # This is because, by default, the HTTP HEAD method should behave just like the GET method,
        # but without a body in the response.
        if hasattr(self, "get") and not hasattr(self, "head"):
            self.head = self.get
        self.request = request
        self.args = args
        self.kwargs = kwargs

    def dispatch(self, request, *args, **kwargs):
        # Try to dispatch to the right method; if a method doesn't exist,
        # defer to the error handler. Also defer to the error handler if the
        # request method isn't on the approved list.
        if request.method.lower() in self.http_method_names:
            handler = getattr(
                self, request.method.lower(), self.http_method_not_allowed
            )
        else:
            handler = self.http_method_not_allowed
        return handler(request, *args, **kwargs)

    def http_method_not_allowed(self, request, *args, **kwargs):
        logger.warning(
            "Method Not Allowed (%s): %s",
            request.method,
            request.path,
            extra={"status_code": 405, "request": request},
        )
        response = HttpResponseNotAllowed(permitted_methods=self._allowed_methods())

        if self.view_is_async:

            async def func():
                return response

            return func()
        else:
            return response

    def options(self, request, *args, **kwargs):
        """Handle responding to requests for the OPTIONS HTTP verb."""
        response = HttpResponse()
        response.headers["Allow"] = ", ".join(self._allowed_methods())
        response.headers["Content-Length"] = "0"

        if self.view_is_async:

            async def func():
                return response

            return func()
        else:
            return response

    def _allowed_methods(self):
        return [m.upper() for m in self.http_method_names if hasattr(self, m)]


class TemplateResponseMixin:
    """A mixin that can be used to render a template."""

    template_name = None
    template_engine = None
    response_class = TemplateResponse
    content_type = None

    def render_to_response(self, context, **response_kwargs):
        """
        Return a response, using the `response_class` for this view, with a
        template rendered with the given context.

        Pass response_kwargs to the constructor of the response class.
        """
        response_kwargs.setdefault("content_type", self.content_type)
        return self.response_class(
            request=self.request,
            template=self.get_template_names(),
            context=context,
            using=self.template_engine,
            **response_kwargs,
        )

    def get_template_names(self):
        """
        Return a list of template names to be used for the request. Must return
        a list. May not be called if render_to_response() is overridden.
        """
        if self.template_name is None:
            raise ImproperlyConfigured(
                "TemplateResponseMixin requires either a definition of "
                "'template_name' or an implementation of 'get_template_names()'"
            )
        else:
            return [self.template_name]


class TemplateView(TemplateResponseMixin, ContextMixin, View):
    """
    Render a template. Pass keyword arguments from the URLconf to the context.
    """

    def get(self, request, *args, **kwargs):
        context = self.get_context_data(**kwargs)
        return self.render_to_response(context)


class RedirectView(View):
    """Provide a redirect on any GET request."""

    permanent = False
    url = None
    pattern_name = None
    query_string = False

    def get_redirect_url(self, *args, **kwargs):
        """
        Return the URL redirect to. Keyword arguments from the URL pattern
        match generating the redirect request are provided as kwargs to this
        method.
        """
        if self.url:
            url = self.url % kwargs
        elif self.pattern_name:
            url = reverse(self.pattern_name, args=args, kwargs=kwargs)
        else:
            return None

        args = self.request.META.get("QUERY_STRING", "")
        if args and self.query_string:
            url = "%s?%s" % (url, args)
        return url

    def get(self, request, *args, **kwargs):
        url = self.get_redirect_url(*args, **kwargs)
        if url:
            if self.permanent:
                return HttpResponsePermanentRedirect(url)
            else:
                return HttpResponseRedirect(url)
        else:
            logger.warning(
                "Gone: %s", request.path, extra={"status_code": 410, "request": request}
            )
            return HttpResponseGone()

    def head(self, request, *args, **kwargs):
        return self.get(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        return self.get(request, *args, **kwargs)

    def options(self, request, *args, **kwargs):
        return self.get(request, *args, **kwargs)

    def delete(self, request, *args, **kwargs):
        return self.get(request, *args, **kwargs)

    def put(self, request, *args, **kwargs):
        return self.get(request, *args, **kwargs)

    def patch(self, request, *args, **kwargs):
        return self.get(request, *args, **kwargs)
