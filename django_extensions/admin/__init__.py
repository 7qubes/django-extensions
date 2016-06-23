# coding=utf-8
#
# Autocomplete feature for admin panel
#
import six
import operator
from functools import update_wrapper
from six.moves import reduce

import django
from django.http import HttpResponse, HttpResponseNotFound
from django.conf import settings
from django.db import models
from django.db.models.query import QuerySet
from django.utils.encoding import smart_str
from django.utils.translation import ugettext as _
from django.utils.text import get_text_list
from django.contrib.admin import ModelAdmin

from django_extensions.admin.widgets import ForeignKeySearchInput
from django_extensions.compat import get_model_compat


class ForeignKeyAutocompleteAdmin(ModelAdmin):
    """Admin class for models using the autocomplete feature.

    There are two additional fields:
       - related_search_fields: defines fields of managed model that
         have to be represented by autocomplete input, together with
         a list of target model fields that are searched for
         input string, e.g.:

         related_search_fields = {
            'author': ('first_name', 'email'),
         }

       - related_string_functions: contains optional functions which
         take target model instance as only argument and return string
         representation. By default __unicode__() method of target
         object is used.

    And also an optional additional field to set the limit on the
    results returned by the autocomplete query. You can set this integer
    value in your settings file using FOREIGNKEY_AUTOCOMPLETE_LIMIT or
    you can set this per ForeignKeyAutocompleteAdmin basis. If any value
    is set the results will not be limited.
    """

    related_search_fields = {}
    related_string_functions = {}
    autocomplete_limit = getattr(settings, 'FOREIGNKEY_AUTOCOMPLETE_LIMIT', None)

    def get_urls(self):
        from django.conf.urls import url

        def wrap(view):
            def wrapper(*args, **kwargs):
                return self.admin_site.admin_view(view)(*args, **kwargs)
            return update_wrapper(wrapper, view)

        # model._meta.module_name is deprecated in django version 1.7 and removed in django version 1.8.
        # It is replaced by model._meta.model_name
        if django.VERSION < (1, 7):
            info = self.model._meta.app_label, self.model._meta.module_name
        else:
            info = self.model._meta.app_label, self.model._meta.model_name

        _url = url(r'foreignkey_autocomplete/$', wrap(self.foreignkey_autocomplete), name='%s_%s_autocomplete' % info)

        # django.conf.urls.patterns is deprecated in django version 1.9 and removed in django version 1.10.
        # It is replaced by a simple Python list
        if django.VERSION < (1, 9):
            from django.conf.urls import patterns
            urlpatterns = patterns('', _url)
        else:
            urlpatterns = [_url]

        urlpatterns += super(ForeignKeyAutocompleteAdmin, self).get_urls()
        return urlpatterns

    def foreignkey_autocomplete(self, request):
        """
        Searches in the fields of the given related model and returns the
        result as a simple string to be used by the jQuery Autocomplete plugin
        """
        query = request.GET.get('q', None)
        app_label = request.GET.get('app_label', None)
        model_name = request.GET.get('model_name', None)
        search_fields = request.GET.get('search_fields', None)
        object_pk = request.GET.get('object_pk', None)

        try:
            to_string_function = self.related_string_functions[model_name]
        except KeyError:
            if six.PY3:
                to_string_function = lambda x: x.__str__()
            else:
                to_string_function = lambda x: x.__unicode__()

        if search_fields and app_label and model_name and (query or object_pk):
            def construct_search(field_name):
                # use different lookup methods depending on the notation
                if field_name.startswith('^'):
                    return "%s__istartswith" % field_name[1:]
                elif field_name.startswith('='):
                    return "%s__iexact" % field_name[1:]
                elif field_name.startswith('@'):
                    return "%s__search" % field_name[1:]
                else:
                    return "%s__icontains" % field_name

            # As of Django 1.7 the 'get_model' method was moved to 'apps'
            model = get_model_compat(app_label, model_name)

            queryset = model._default_manager.all()
            data = ''
            if query:
                for bit in query.split():
                    or_queries = [models.Q(**{construct_search(smart_str(field_name)): smart_str(bit)}) for field_name in search_fields.split(',')]
                    other_qs = QuerySet(model)
                    other_qs.query.select_related = queryset.query.select_related
                    other_qs = other_qs.filter(reduce(operator.or_, or_queries))
                    queryset = queryset & other_qs

                additional_filter = self.get_related_filter(model, request)
                if additional_filter:
                    queryset = queryset.filter(additional_filter)

                if self.autocomplete_limit:
                    queryset = queryset[:self.autocomplete_limit]

                data = ''.join([six.u('%s|%s\n') % (to_string_function(f), f.pk) for f in queryset])
            elif object_pk:
                try:
                    obj = queryset.get(pk=object_pk)
                except:
                    pass
                else:
                    data = to_string_function(obj)
            return HttpResponse(data)
        return HttpResponseNotFound()

    def get_related_filter(self, model, request):
        """Given a model class and current request return an optional Q object
        that should be applied as an additional filter for autocomplete query.
        If no additional filtering is needed, this method should return
        None."""

    def get_help_text(self, field_name, model_name):
        searchable_fields = self.related_search_fields.get(field_name, None)
        if searchable_fields:
            help_kwargs = {
                'model_name': model_name,
                'field_list': get_text_list(searchable_fields, _('and')),
            }
            return _('Use the left field to do %(model_name)s lookups in the fields %(field_list)s.') % help_kwargs
        return ''

    def formfield_for_dbfield(self, db_field, **kwargs):
        """
        Overrides the default widget for Foreignkey fields if they are
        specified in the related_search_fields class attribute.
        """
        if isinstance(db_field, models.ForeignKey) and db_field.name in self.related_search_fields:
            model_name = db_field.rel.to._meta.object_name
            help_text = self.get_help_text(db_field.name, model_name)
            if kwargs.get('help_text'):
                help_text = six.u('%s %s' % (kwargs['help_text'], help_text))
            kwargs['widget'] = ForeignKeySearchInput(db_field.rel, self.related_search_fields[db_field.name])
            kwargs['help_text'] = help_text
        return super(ForeignKeyAutocompleteAdmin, self).formfield_for_dbfield(db_field, **kwargs)
