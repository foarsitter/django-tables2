import warnings

from django.utils.functional import cached_property

from .utils import OrderBy, OrderByTuple, segment


class TableData:
    """
    Base class for table data containers.
    """

    def __init__(self, data):
        self.data = data

    def __getitem__(self, key):
        """
        Slicing returns a new `.TableData` instance, indexing returns a single record.
        """
        return self.data[key]

    def __iter__(self):
        """
        for ... in ... default to using this. There's a bug in Django 1.3
        with indexing into QuerySets, so this side-steps that problem (as well
        as just being a better way to iterate).
        """
        return iter(self.data)

    def set_table(self, table):
        """
        `Table.__init__` calls this method to inject an instance of itself into the
        `TableData` instance.
        Good place to do additional checks if Table and TableData instance will work
        together properly.
        """
        self.table = table

    @property
    def model(self):
        return getattr(self.data, "model", None)

    @property
    def ordering(self):
        return None

    @property
    def verbose_name(self):
        return "item"

    @property
    def verbose_name_plural(self):
        return "items"

    @staticmethod
    def from_data(data):
        # allow explicit child classes of TableData to be passed to Table()
        if isinstance(data, TableData):
            return data
        if TableQuerysetData.validate(data):
            return TableQuerysetData(data)
        elif TableListData.validate(data):
            return TableListData(list(data))

        raise ValueError(
            "data must be QuerySet-like (have count() and order_by()) or support"
            " list(data) -- {} has neither".format(type(data).__name__)
        )


class TableListData(TableData):
    """
    Table data container for a list of dicts, for example::

    [
        {'name': 'John', 'age': 20},
        {'name': 'Brian', 'age': 25}
    ]

    .. note::

        Other structures might have worked in the past, but are not explicitly
        supported or tested.
    """

    @staticmethod
    def validate(data):
        """
        Validates `data` for use in this container
        """
        return hasattr(data, "__iter__") or (
            hasattr(data, "__len__") and hasattr(data, "__getitem__")
        )

    def __len__(self):
        return len(self.data)

    @property
    def verbose_name(self):
        return getattr(self.data, "verbose_name", super().verbose_name)

    @property
    def verbose_name_plural(self):
        return getattr(self.data, "verbose_name_plural", super().verbose_name_plural)

    def order_by(self, aliases):
        """
        Order the data based on order by aliases (prefixed column names) in the
        table.

        Arguments:
            aliases (`~.utils.OrderByTuple`): optionally prefixed names of
                columns ('-' indicates descending order) in order of
                significance with regard to data ordering.
        """
        accessors = []
        for alias in aliases:
            bound_column = self.table.columns[OrderBy(alias).bare]

            # bound_column.order_by reflects the current ordering applied to
            # the table. As such we need to check the current ordering on the
            # column and use the opposite if it doesn't match the alias prefix.
            if alias[0] != bound_column.order_by_alias[0]:
                accessors += bound_column.order_by.opposite
            else:
                accessors += bound_column.order_by

        self.data.sort(key=OrderByTuple(accessors).key)


class TableQuerysetData(TableData):
    """
    Table data container for a queryset.
    """

    @staticmethod
    def validate(data):
        """
        Validates `data` for use in this container
        """
        return (
            hasattr(data, "count")
            and callable(data.count)
            and hasattr(data, "order_by")
            and callable(data.order_by)
        )

    def __len__(self):
        """Cached data length"""
        if not hasattr(self, "_length") or self._length is None:
            if hasattr(self.table, "paginator"):
                if self.table.paginator.object_list:
                    # use the cached property when there are objects
                    self._length = self.table.paginator.count
                else:
                    # for paginated tables, use QuerySet.count() as we are interested in total number of records.
                    self._length = self.data.count()
            else:
                # for non-paginated tables, use the length of the QuerySet
                self._length = len(self.data)

        return self._length

    def set_table(self, table):
        super().set_table(table)
        if self.model and getattr(table._meta, "model", None) and self.model != table._meta.model:
            warnings.warn(
                "Table data is of type {} but {} is specified in Table.Meta.model".format(
                    self.model, table._meta.model
                )
            )

    @property
    def ordering(self):
        """
        Returns the list of order by aliases that are enforcing ordering on the
        data.

        If the data is unordered, an empty sequence is returned. If the
        ordering can not be determined, `None` is returned.

        This works by inspecting the actual underlying data. As such it's only
        supported for querysets.
        """

        aliases = {}
        for bound_column in self.table.columns:
            aliases[bound_column.order_by_alias] = bound_column.order_by
        try:
            return next(segment(self.data.query.order_by, aliases))
        except StopIteration:
            pass

    def order_by(self, aliases):
        """
        Order the data based on order by aliases (prefixed column names) in the
        table.

        Arguments:
            aliases (`~.utils.OrderByTuple`): optionally prefixed names of
                columns ('-' indicates descending order) in order of
                significance with regard to data ordering.
        """
        modified_any = False
        accessors = []
        for alias in aliases:
            bound_column = self.table.columns[OrderBy(alias).bare]
            # bound_column.order_by reflects the current ordering applied to
            # the table. As such we need to check the current ordering on the
            # column and use the opposite if it doesn't match the alias prefix.
            if alias[0] != bound_column.order_by_alias[0]:
                accessors += bound_column.order_by.opposite
            else:
                accessors += bound_column.order_by

            if bound_column:
                queryset, modified = bound_column.order(self.data, alias[0] == "-")

                if modified:
                    self.data = queryset
                    modified_any = True

        # custom ordering
        if modified_any:
            return

        # Traditional ordering
        if accessors:
            order_by_accessors = (a.for_queryset() for a in accessors)
            self.data = self.data.order_by(*order_by_accessors)

    @cached_property
    def verbose_name(self):
        """
        The full (singular) name for the data.

        Model's `~django.db.Model.Meta.verbose_name` is honored.
        """
        return self.data.model._meta.verbose_name

    @cached_property
    def verbose_name_plural(self):
        """
        The full (plural) name for the data.

        Model's `~django.db.Model.Meta.verbose_name` is honored.
        """
        return self.data.model._meta.verbose_name_plural
