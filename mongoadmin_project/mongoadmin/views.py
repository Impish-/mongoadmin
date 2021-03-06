#encoding: utf-8
from urllib.parse import urlencode

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.paginator import Paginator
from django.forms import model_to_dict
from django.http import HttpResponseRedirect, Http404
from django.utils.decorators import method_decorator
from django.views.generic import DetailView, TemplateView, CreateView, ListView
from django.views.generic.detail import SingleObjectMixin
from django.views.generic.edit import FormMixin, ProcessFormView, FormView
from django.shortcuts import get_object_or_404

import pymongo
from bson.objectid import ObjectId
from bson import json_util

import cgi
import json
import urllib

from mongoadmin_project.mongoadmin.models import MongoConnection
from ..common.utils import ellipsize
from . import models, forms


@login_required
def redirect_to(request, id):
    connection = MongoConnection.objects.get(id=id)
    request.session['mongoconnection'] = model_to_dict(connection)
    return HttpResponseRedirect('/mongo/session/') # TODO: Надо на реверсеры все


@method_decorator([login_required], name='dispatch')
class СonnectsList(ListView):
    model = MongoConnection
    context_object_name = 'connects'


@method_decorator([login_required], name='dispatch')
class ConnectView(CreateView):
    model = MongoConnection
    form_class = forms.ConnectForm
    template_name = 'mongoadmin/connect.html'
    success_url = '/mongo/session/'

    def form_valid(self, form):
        form.user = self.request.user
        super(ConnectView, self).form_valid(form)
        # don't save the form. store connection in session.
        connection = self.object

        try:
            test_result = connection.get_connection().server_info()
        except pymongo.errors.AutoReconnect as e:
            messages.error(self.request, e)
            return self.form_invalid(form)

        self.request.session['mongoconnection'] = model_to_dict(connection)
        if connection.database:
            # TODO: escape?
            return HttpResponseRedirect('%s%s' % (self.success_url, connection.database))
        else:
            return HttpResponseRedirect(self.success_url)

@method_decorator([login_required], name='dispatch')
class ConnectionDetailMixin(object):
    model = models.MongoConnection

    def dispatch(self, request, *args, **kwargs):
        try:
            return super(ConnectionDetailMixin, self).dispatch(request, *args, **kwargs)
        except pymongo.errors.AutoReconnect as e:
            messages.error(self.request, e)
            return self.response_class(
                request=self.request,
                template='mongoadmin/error.html',
                context={},
             )

    def setup_connection(self):
        if self.kwargs['connection_name'] == 'session':
            try:
                self.connection = get_object_or_404(models.MongoConnection, name= self.request.session['mongoconnection']['name'],
                                                    id= self.request.session['mongoconnection']['id'])
            except KeyError:
                raise Http404
        else:
            if not self.request.user.is_authenticated():
                raise Http404
            self.connection = get_object_or_404(models.MongoConnection, name=self.kwargs['connection_name'], user=self.request.user)
            # self.connection = self.connection.get_connection()
        if 'database_name' in self.kwargs:
            self.database = self.connection.get_connection()[self.kwargs['database_name']]
            if 'collection_name' in self.kwargs:
                self.collection = self.database[self.kwargs['collection_name']]


@method_decorator([login_required], name='dispatch')
class ConnectionView(ConnectionDetailMixin, TemplateView):
    template_name = 'mongoadmin/connection.html'

    def get_context_data(self, **kwargs):
        context = super(ConnectionView, self).get_context_data(**kwargs)
        self.setup_connection()
        try:
            databases = self.connection.get_connection().database_names()
        except pymongo.errors.AutoReconnect as e:
            messages.error(self.request, e)
            databases = []
        context.update({
            'connection': self.connection,
            'databases': databases,
        })
        return context


@method_decorator([login_required], name='dispatch')
class DatabaseView(ConnectionDetailMixin, TemplateView):
    template_name = 'mongoadmin/database.html'

    def get_context_data(self, **kwargs):
        context = super(DatabaseView, self).get_context_data(**kwargs)
        self.setup_connection()
        try:

            collections = self.database.collection_names()
        except pymongo.errors.AutoReconnect as e:
            messages.error(self.request, e)
            collections = []
        context.update({
            'connection': self.connection,
            'database': self.kwargs['database_name'],
            'collections': collections,
        })
        return context


@method_decorator([login_required], name='dispatch')
class CollectionView(ConnectionDetailMixin, TemplateView):
    template_name = 'mongoadmin/collection.html'
    per_page = 50

    def get_context_data(self, **kwargs):
        context = super(CollectionView, self).get_context_data(**kwargs)

        form = forms.CollectionFilterForm(self.request.GET)
        if form.is_valid():
            page_number = form.cleaned_data['page']
            query = form.cleaned_data['query']
            fields = form.cleaned_data['fields']
        else:
            page_number = 1
            query = None
            fields = None
        if not page_number:
            page_number = 1


        self.setup_connection()

        all_documents = self.collection.find(query)

        paginator = Paginator(all_documents, self.per_page)
        page_obj = paginator.page(page_number)

        documents = page_obj.object_list

        params = dict(cgi.parse_qsl(self.request.META.get('QUERY_STRING')))
        if 'page' in params:
            del params['page']

        getvars = urlencode(params)
        if getvars:
            getvars = '&%s' % getvars

        def get_field(data, field):
            for part in field.split('.'):
                if data:
                    data = data.get(part)
                else:
                    break
            return data

        def prepare_document(document):
            document_id = document.pop('_id')
            if fields:
                document_fields = [json.dumps(get_field(document, field), default=json_util.default) for field in fields]
            else:
                document_fields = []
            return document_id, ellipsize(json.dumps(document, default=json_util.default), 120), document_fields

        documents_list = [prepare_document(document) for document in documents]

        context.update({
            'connection': self.connection,
            'database': self.kwargs['database_name'],
            'collection': self.kwargs['collection_name'],
            'documents': documents_list,
            'paginator': paginator,
            'page_obj': page_obj,
            'is_paginated': paginator.num_pages > 1,
            'display_page_links': paginator.num_pages < 20,
            'getvars': getvars,
            'form': form,
            'query': query,
            'fields': fields,
        })
        return context


@method_decorator([login_required], name='dispatch')
class BaseDocumentView(ConnectionDetailMixin):
    def get_document(self):
        document = self.collection.find_one({'_id': ObjectId(self.kwargs['pk'])})
        return document

    def get(self, request, *args, **kwargs):
        self.setup_connection()
        self.document = self.get_document()
        return super(BaseDocumentView, self).get(request, *args, **kwargs)

    def post(self, request, *args, **kwargs):
        self.setup_connection()
        self.document = self.get_document()
        return super(BaseDocumentView, self).post(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super(BaseDocumentView, self).get_context_data(**kwargs)
        context.update({
            'connection': self.connection,
            'database': self.kwargs['database_name'],
            'collection': self.kwargs['collection_name'],
            'document_id': self.document and str(self.document['_id']) or '',
        })
        return context


@method_decorator([login_required], name='dispatch')
class UpdateDocumentView(BaseDocumentView, FormView):
    form_class = forms.DocumentForm
    template_name = 'mongoadmin/document.html'

    def form_valid(self, form):
        obj = form.cleaned_data['json']
        id = form.cleaned_data['id']
        if id and '_id' not in obj:
            # Reinsert id into document when editing an existing document.
            obj['_id'] = ObjectId(id)
        id = self.collection.save(obj)
        messages.success(self.request, 'The document %s was saved successfully.' % id)
        if '_continue' in self.request.POST:
            return HttpResponseRedirect('../%s/' % id)
        elif '_addanother' in self.request.POST:
            return HttpResponseRedirect('../add/')
        else:
            return HttpResponseRedirect('../')

    def get_initial(self):
        # document = self.document.copy()
        # del document['_id']
        # json_data = json.dumps(document, default=json_util.default)
        json_data = json.dumps(self.document, default=json_util.default)
        return {
            'json': json_data,
            'id': str(self.document['_id']),
        }


@method_decorator([login_required], name='dispatch')
class CreateDocumentView(UpdateDocumentView):
    def get_document(self):
        return None

    def get_initial(self):
        return {
            'json': '{}',
            'id': '',
        }


@method_decorator([login_required], name='dispatch')
class DeleteDocumentView(BaseDocumentView, TemplateView):
    template_name = 'mongoadmin/document_delete.html'

    def post(self, request, *args, **kwargs):
        self.setup_connection()
        self.document = self.get_document()
        self.collection.remove(ObjectId(self.kwargs['pk']))
        messages.success(self.request, 'The document %s was removed successfully.' % self.kwargs['pk'])
        return HttpResponseRedirect('../../')
