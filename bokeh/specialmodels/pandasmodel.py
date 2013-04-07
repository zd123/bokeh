import uuid
import base64
import cPickle as pickle
import numpy as np
import datetime as dt

from ..bbmodel import ContinuumModel, register_type
from ..data import make_source
import pandas

class PandasDataSource(ContinuumModel):
    # FIXME: this is a little redundant with pandas_plot_data.py..
    # we should figure out how to unify this later
    """PandasDataSource.  This class represents serverside data.
    When you use it, you should a PandasPivotModel, or a PandasPlotSource
    at it.
    attributes:
        pickled : pickled dataframe.
    you can also pass in df, which will be pickled, but df will not be
    stored as a backbone attribute
    """
    hidden_fields = ('encoded',)
    def __init__(self, typename, **kwargs):
        if 'df' in kwargs:
            df = kwargs.pop('df')
            kwargs['encoded'] = base64.b64encode(pickle.dumps(df, -1))
            self.data = df
        super(PandasDataSource, self).__init__(typename, **kwargs)
        
    def set_data(self):
        if hasattr(self, 'data'):
            self.set('encoded', base64.b64encode(pickle.dumps(self.data, -1)))
        
    def ensure_data(self):
        if not hasattr(self, 'data'):
            self.data = pickle.loads(base64.b64decode(self.get('encoded')))
            
    def selected(self):
        self.pull()
        return self.get('selected')
    
class PandasPlotSource(ContinuumModel):
    """
    attributes:
        pandassource : reference to PandasDataSource
        data : dataframe output
    """
    def __init__(self, typename, **kwargs):
        if 'pandassourceobj' in kwargs:
            self.pandassource = kwargs.pop('pandassourceobj')
            kwargs['pandassource'] = self.pandassource.ref()
        super(PandasPlotSource, self).__init__(typename, **kwargs)
        
    def get_data(self):
        if not hasattr(self, 'pandassource'):
            self.pandassource = self.client.get(
                self.get('pandassource')['type'],
                self.get('pandassource')['id'],
                include_hidden=True
                )
        if not hasattr(self, 'inputwidget') and self.get('inputwidget'):
            self.inputwidget = self.client.get(self.get('inputwidget')['type'],
                                               self.get('inputwidget')['id'],
                                               )
        self.pandassource.ensure_data()
        data = self.pandassource.data
        return data

    def to_json(self, include_hidden=False):
        data = self.get_data()
        data = make_source(index=data.index, **data)
        self.set('data', data)
        return self.attributes

class PandasPivotModel(PandasPlotSource):
    """Pandas Pivot table class
    you must pass either a client in to kwargs, or pandassourceobj
    which is an instance of PandasDataSource
    
    attributes:
        pandassource : reference to PandasDataSource
        sort : list of columns to sort by
        groups : list of columns to group by (not implemented)
        agg : agg function (not implemented)
        offset : offset for pagination
        length : length of data for pagination
        data : dataframe output
        precision : column names to precision mapping
    """
    defaults = {
        'offset' : 0,
        'length' : 100,
        'groups' : [],
        'sort' : [],
        'agg' : 'sum',
        'selection' : [],
        'computed_columns' : [],
        }
    def get_selection(self):
        """computes selection of aggregated data from pandassourceobj
        """
    def get_slice(self, data):
        data = data[self.get('offset'):self.get('offset')+self.get('length')]
        return data
    
    def format_data(self, jsondata):
        """inplace manipulation of jsondata
        """
        precision = self.get('precision', {})
        for dp in jsondata:
            for k in dp:
                if isinstance(dp[k], float):
                    pass
                    #dp[k] = "%%.%df" % precision.get(k,2) % dp[k]
                elif isinstance(dp[k], (dt.date, dt.datetime)):
                    dp[k] = dp[k].isoformat()
    def convert_type(self, inputtype, item):
        if inputtype == 'int':
            return int(item)
        elif inputtype == 'float':
            return float(item)
        elif inputtype == 'str':
            return item
        
    def computed_column(self, data, column_spec):
        localvars = dict(**data)
        if hasattr(self, 'inputwidget'):
            for fld in self.inputwidget.get('fields'):
                localvars[fld['name']] = self.convert_type(fld['type'], fld['val'])
        localvars['pd'] = pandas
        result = eval(column_spec['code'], localvars)
        data[column_spec['name']] = result
        
    def get_data(self):
        data = super(PandasPivotModel, self).get_data()
        #add counts/selected, so we can compute counts and selections
        #we will pop them off later
        
        data['_counts'] = np.ones(len(data))
        data['_selected'] = np.zeros(len(data))
        for column_spec in self.get('computed_columns'):
            self.computed_column(data, column_spec)
        raw_selected = self.pandassource.get('selected', []) # integer list
        data.ix[raw_selected, '_selected'] = 1
        if self.get('groups') and self.get('agg'):
            self.groupobj = data.groupby(self.get('groups'))
            data = getattr(self.groupobj, self.get('agg'))()
        else:
            self.groupobj = None
        sort = self.get('sort')
        if sort:
            columns = [x['column'] for x in sort]
            ascending = [x['ascending'] for x in sort]
            data = data.sort(
                columns=columns,
                ascending=ascending
                )
        self.fulldata = data
        if np.sum(data._selected) > 1 and not self.groupobj:
            # in the non group by case, we filter on selection
            # otherwise we output the # of selected items
            data = data.ix[data._selected==1, :]
        self.totallength = len(data)

        #adjust lengths
        if self.get('maxlength') and self.totallength > self.get('maxlength'):
            #if the data set grows, reset length
            self.set('length', 100)
        self.set('maxlength', self.totallength)
        if self.get('offset') > self.get('maxlength'):
            self.set('offset', 0)
        self.set('length', min(
            self.get('length'),
            self.get('maxlength') - self.get('offset'))
                 )
        
        self.data = self.get_slice(data)
        self.data.pop('_counts')
        self.data.pop('_selected')
        return self.data
    
    def to_json(self, include_hidden=False):
        data = self.get_data()
        if self.groupobj:
            stats = self.get_slice(self.groupobj.sum())
            counts = stats['_counts'].tolist()
            selected = stats['_selected'].tolist()
        else:
            counts = None
            selected = self.get_slice(self.fulldata)['_selected']
        self.set('index', data.index.tolist())
        columns = data.columns.tolist()
        data = make_source(**data)
        self.format_data(data)
        
        #self.set('selected', selected)
        self.set('data', data)
        self.set('columns', columns)
        self.set('counts', counts)
        return self.attributes

register_type('PandasPivot', PandasPivotModel)
register_type('PandasDataSource', PandasDataSource)
register_type('PandasPlotSource', PandasPlotSource)