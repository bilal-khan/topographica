"""
The Topographica Lancet extension allows Topographica simulations to
be easily integrated into a Lancet workflow (see
github.com/ioam/lancet). The TopoCommand CommandTemplate is
appropriate for simple runs using the default analysis function,
whereas the Analysis and RunBatchCommand allow for more sophisticated
measurements and analysis to be executed during a simulation run.
"""

import os, sys, types, pickle, inspect

try:  # The correct thing to do in Python 2.7+
   from importlib import import_module
except:

   def import_module(name):
      "Fallback equivalent to the function in importlib"
      __import__(name)
      return sys.modules[name]

from collections import namedtuple

import topo
import param

from lancet import PrettyPrinted
from lancet import CommandTemplate
from lancet import Launcher, review_and_launch
from lancet import NumpyFile


from topo.misc.commandline import default_output_path
review_and_launch.output_directory = default_output_path()
Launcher.output_directory = default_output_path()


class param_formatter(param.ParameterizedFunction):
   """
   This class is closely related to the param_formatter class in
   topo/command/__init__.py.  Like that default class, it formats
   parameters as a string for use in a directory name. Unlike that
   default class, it does not use the parameters repr methods but the
   exact, succinct commandline representation as returned by a Lancet
   Args object.

   This version has several advantages over the default:

   - It formats values exactly as they appear in a command. For
   example, a value specified as 6.00 on the commandline remains this
   way and is never represented to higher precision or with floating
   point error.

   - Parameters are sorted from slowest to fastest varying or
   (optionally) alphanumerically by default.

   - It allows for a custom separator and an optional trunctation
   length for values.

   - By default, formats a string only for the parameters that are
   varying (may be toggled).
   """

   abbreviations = param.Dict(default={}, doc='''
       A dictionary of abbreviations to use of type {<key>:<abbrev>}.
       If a specifier key has an entry in the dictionary, the
       abbreviation is used.  Useful for shortening long parameter
       names in the directory structure.''')

   alphanumeric_sort = param.Boolean(default=False, doc='''
        Whether to sort the (potentially abbreviated) keys
        alphabetically or not. By default, keys are ordered from
        slowest varying to fastest varying using thr information
        provided by Lancet's Args object.''')

   format_constant_keys = param.Boolean(default=False, doc='''
        Whether to represent parameters that are known to be constant
        across batches.''')

   truncation_limit = param.Number(default=None, allow_None=True, doc= '''
        If None, no truncation is performed, otherwise specifies the
        maximum length of any given specification value.''')

   separator = param.String(default=',', doc="""
          The separator to use between <key>=<value> pairs.""")


   def __call__(self, constant_keys, varying_keys, spec):

      ordering = (constant_keys if self.format_constant_keys else []) + varying_keys
      if self.alphanumeric_sort:  ordering = sorted(ordering)
      abbreved = [(self.abbreviations.get(k,k), spec[k]) for k in ordering]
      return self.separator.join(['%s=%s' % (k, v[:self.truncation_limit]) for (k,v) in abbreved])



class TopoCommand(CommandTemplate):
   """
   TopoCommand is designed to to format Lancet Args objects into
   run_batch commands in a general way. Note that Topographica is
   always invoked with the -a flag so all of topo.command is imported.

   Some of the parameters duplicate those in run_batch to ensure
   consistency with previous run_batch usage in Topographica. As a
   consequence, this class sets all the necessary options for
   run_batch except the 'times' parameter which may vary specified
   arbitrarily by the Lancet Args object.
   """

   tyfile = param.String(doc="The Topographica model file to run.")

   analysis_fn = param.String(default="default_analysis_function", doc="""
       The name of the analysis_fn to run. If modified from the
       default, the named callable will need to be imported into the
       namespace using a '-c' command in topo_flag_options.""")

   tag = param.Boolean(default=False, doc="""
       Whether to label the run_batch generated directory with the
       batch name and batch tag.""")

   topo_switches = param.List(default=['-a'], doc = """
          Specifies the Topographica qsub switches (flags without
          arguments) as a list of strings. Note the that the -a switch
          is always used to auto import commands.""")

   topo_flag_options = param.Dict(default={}, doc="""
          Specifies Topographica flags and their corresponding options
          as a dictionary.  This parameter is suitable for setting -c
          and -p flags for Topographica. This parameter is important
          for introducing the callable named by the analysis_fn
          parameter into the namespace.

          Tuples can be used to indicate groups of options using the
          same flag:
          {'-p':'retina_density=5'} => -p retina_density=5
          {'-p':('retina_density=5', 'scale=2') => -p retina_density=5 -p scale=2

          If a plain Python dictionary is used, the keys are
          alphanumerically sorted, otherwise the dictionary is assumed
          to be an OrderedDict (Python 2.7+, Python3 or
          param.external.OrderedDict) and the key ordering will be
          preserved. Note that the '-' is prefixed to the key if
          missing (to ensure a valid flag). This allows keywords to be
          specified with the dict constructor eg.. dict(key1=value1,
          key2=value2).""")

   param_formatter = param.Callable(param_formatter.instance(),
      doc="""Used to specify run_batch formatting.""")

   max_name_length= param.Number(default=200, doc="Matches run_batch parameter of same name.")

   snapshot = param.Boolean(default=True, doc="Matches run_batch parameter of same name.")

   vc_info = param.Boolean(default=True, doc="Matches run_batch parameter of same name.")

   save_global_params = param.Boolean(default=True, doc="Matches run_batch parameter of same name.")


   def __init__(self, tyfile, executable=None, **kwargs):

      auto_executable =  os.path.realpath(
         os.path.join(topo.__file__, '..', '..', 'topographica'))

      executable = executable if executable else auto_executable
      super(TopoCommand, self).__init__(tyfile=tyfile, executable=executable, **kwargs)
      self.pprint_args(['executable', 'tyfile', 'analysis_fn'],['topo_switches', 'snapshot'])
      self._typath = os.path.abspath(self.tyfile)

      if not os.path.isfile(self.executable):
         raise Exception('Cannot find the topographica script relative to topo/__init__.py.')
      if not os.path.exists(self._typath):
         raise Exception("Tyfile doesn't exist! Cannot proceed.")

      if ((self.analysis_fn.strip() != "default_analysis_function")
          and (type(self) == TopoCommand)
          and ('-c' not in self.topo_flag_options)):
         raise Exception, 'Please use -c option to introduce the appropriate analysis into the namespace.'


   def _topo_args(self, switch_override=[]):
      """
      Method to generate Popen style argument list for Topographica
      using the topo_switches and topo_flag_options
      parameters. Switches are returned first, sorted
      alphanumerically.  The qsub_flag_options follow in the order
      given by keys() which may be controlled if an OrderedDict is
      used (eg. in Python 2.7+ or using param.external
      OrderedDict). Otherwise the keys are sorted alphanumerically.
      """
      opt_dict = type(self.topo_flag_options)()
      opt_dict.update(self.topo_flag_options)

      # Alphanumeric sort if vanilla Python dictionary
      if type(self.topo_flag_options) == dict:
            ordered_options = [(k, opt_dict[k]) for k in sorted(opt_dict)]
      else:
         ordered_options =  list(opt_dict.items())

      # Unpack tuple values so flag:(v1, v2,...)) => ..., flag:v1, flag:v2, ...
      unpacked_groups = [[(k,v) for v in val] if type(val)==tuple else [(k,val)]
                           for (k,val) in ordered_options]
      unpacked_kvs = [el for group in unpacked_groups for el in group]

      # Adds '-' if missing (eg, keywords in dict constructor) and flattens lists.
      ordered_pairs = [(k,v) if (k[0]=='-') else ('-%s' % (k), v)
                       for (k,v) in  unpacked_kvs]
      ordered_options = [[k]+([v] if type(v) == str else v)
                         for (k,v) in ordered_pairs]
      flattened_options = [el for kvs in ordered_options for el in kvs]
      switches =  [s for s in switch_override
                   if (s not in self.topo_switches)] + self.topo_switches
      return sorted(switches) + flattened_options


   def _run_batch_kwargs(self, spec, tid, info):
      """
      Defines the keywords accepted by run_batch and so specifies
      run_batch behaviour. These keywords are those consumed by
      run_batch for controlling run_batch behaviour.
      """
      # Direct options for controlling run_batch.
      options = {'name_time_format':   repr(info['timestamp_format']),
                 'max_name_length':    self.max_name_length,
                 'snapshot':           self.snapshot,
                 'vc_info':            self.vc_info,
                 'save_global_params': self.save_global_params,
                 'metadata_dir':       repr('metadata')}

      # Settings inferred using information from launcher ('info')
      tag_info = (info['batch_name'], info['batch_tag'])
      tag = '[%s]_' % ':'.join(el for el in tag_info if el) if self.tag else ''

      derived_options = {'dirname_prefix':  repr(''),
                         'tag':             repr('%st%s_' % (tag, tid)),
                         'output_directory':repr(info['root_directory'])}

      # Use fixed timestamp argument to run_batch if available.
      if info['timestamp'] is not None:
         derived_options['timestamp'] = info['timestamp']

      # The analysis_fn is set my self.analysis_fn
      derived_options['analysis_fn'] = self.analysis_fn

      # Use the specified param_formatter to create the suitably named
      # lambda (returning the desired string) in run_batch.
      dir_format = self.param_formatter(info['constant_keys'],
                                        info['varying_keys'], spec)

      dir_formatter = 'lambda p: %s' % repr(dir_format)
      derived_options['dirname_params_filter'] =  dir_formatter

      return dict(options.items() + derived_options.items())


   def __call__(self, spec, tid=None, info={}):
      """
      Returns a Popen argument list to invoke Topographica and execute
      run_batch with all options appropriately set (in alphabetical
      order). Keywords that are not run_batch options are also in
      alphabetical order at the end of the keyword list.
      """

      kwarg_opts = self._run_batch_kwargs(spec, tid, info)
      # Override spec values if mistakenly included.
      allopts = dict(spec,**kwarg_opts)

      keywords = ', '.join(['%s=%s' % (k,allopts[k]) for k in
                            sorted(kwarg_opts.keys())+sorted(spec.keys())])

      run_batch_list = ["run_batch(%s,%s)" % (repr(self._typath), keywords)]
      topo_args = self._topo_args(['-a'])
      return  [self.executable] + topo_args + ['-c',  '; '.join(run_batch_list)]



class AnalysisFn(PrettyPrinted, object):
   """
   An AnalysisFn records information about a function so that run_batch
   can invoke it via an Analysis object. It also allows checks on the
   function's signature using Python's introspection mechanisms.

   The supplied function may return any data type and may be a Python
   function or a parameterized function class (but not an instance).
   """

   def __init__(self, fn):
      name, argspec, signature = self._register(fn)
      self.module = str(fn.__module__)
      self.name = name
      self.argspec = argspec
      self.signature = signature

      # Would be good to check if module is available


   def _register(self, fn):
      """
      Helper method to establish function name and determine the
      argument signature. Works with both Python functions and
      ParameterizedFunctions (but not instances).
      """
      if isinstance(fn, param.ParameterizedFunction):
         raise Exception("Parameterized function instances not supported")

      parameterizedfn = (not isinstance(fn, types.FunctionType)
                         and issubclass(fn, param.ParameterizedFunction))
      name = fn.__name__ if parameterizedfn else fn.__name__
      func = fn.__call__ if  parameterizedfn else fn
      argspec = inspect.getargspec(func)
      (names, alist, kwdict, defaults) = argspec
      args =  names[-len(defaults) if defaults else 0:]
      kwargs = names[:-len(defaults) if defaults else 0]

      if parameterizedfn:
         args = [arg for arg in args if arg!='self']
         kwargs = [k for k in (kwargs + fn.params().keys())
                   if k not in ['name', 'self']]
      return name, argspec, (args, kwargs, alist, kwdict)


   def get_module(self):
      """Return a module object that contains the analysis function."""
      return import_module(self.module)


   def __repr__(self):
      """Used for pretty printing declaratively."""
      return "AnalysisFn(%s.%s)" % (self.module, self.name)



class Analysis(PrettyPrinted, param.Parameterized):
   """
   Analysis is a callable that behaves like a general analysis_fn for
   run_batch. You can add multiple analysis functions, the return
   values of which will be collated and saved as a numpy file
   containing topo.sim.time metadata as well as any extra metadata
   supplied by the analysis functions.
   """

   paths = param.List(default=[], doc="""
       Extra directories to add to the sys.path before trying to load
       the analysis functions.""")

   strict_verify = param.Boolean(default=False, doc="""
       Makes sure no keywords are allowed to fallback to their default
       values for any of the analysis functions.""")

   analysis_fns = param.List(default=[], doc="""
       The list of analysis functions to execute in run_batch""")

   metadata = param.List(default=[], doc="""
       Keys to include as metadata in the output numpy file along with
       'time' (Topographica simulation time).""")


   @classmethod
   def pickle_path(cls, batch_info):
      """
      Locates the pickle file based on the given launch info
      dictionary. Used by load as a classmethod and by save as an
      instance method.
      """
      pkl_name = '%s.analysis' % batch_info['batch_name']
      return os.path.join(batch_info['root_directory'], pkl_name)


   @classmethod
   def load(cls, tid, batch_info, specs):
      """
      Classmethod used to load the RunBatchCommand callable into a
      Topographica run_batch context. Loads the pickle file based on
      the batch_name and root directory in batch_info.
      """

      pkl_path = cls.pickle_path(batch_info)
      with open(pkl_path,'rb') as pkl: analysis =  pickle.load(pkl)

      sys.path += analysis.paths
      callable_specs  = [(afn.name, afn.get_module())
                         for afn in analysis.analysis_fns]
      analysis._callables = dict((name, getattr(module, name))
                                 for (name, module) in callable_specs)
      runtime_info = namedtuple('runtime_info','tid batch_info specs')
      analysis._runtime_info = runtime_info(tid, batch_info, specs)
      return analysis


   def __init__(self, **kwargs):
      self._pprint_args = ([],[],None,{})
      super(Analysis, self).__init__(**kwargs)
      #self.pprint_args(['analysis_fns','paths'],['strict_verify'])
      # Information about the batch.
      self._runtime_info = ()
      # The callables specified by analysis_fns
      self._callables = {}
      # The data and metadata accumulators
      self._data = {}
      self._metadata = {}


   def add_analysis_fn(self, fn):
      """
      Add an analysis function of type AnalysisFn. If path is not
      None, the module needs to be on the path.

      If the return type of the function is a dictionary, the keys are
      assumed to be labels for data. If there is a key 'metadata' that
      holds a dictionary, those items will be merged into the saved
      metadata dictionary.
      """
      sys_paths = sys.path[:]
      sys.path += self.paths
      self.analysis_fns.append(AnalysisFn(fn))
      sys.path = sys_paths


   def __call__(self):
      """
      Calls the necessary analysis functions specified by the user in
      the run_batch context. Invoked as a single analysis function on
      the commandline by RunBatchCommand.
      """

      info = self._runtime_info
      batch_tag = info.batch_info['batch_tag']
      batch_name = info.batch_info['batch_name']

      topo_time = topo.sim.time()
      metadata_items = [(key, info.specs[key]) for key in self.metadata]
      self._metadata = dict(metadata_items + [('time',topo_time)])

      if not batch_tag: filename = '%s_%s' % (batch_name, topo_time)
      else: filename = '%s[%s]_%s' % (batch_name, batch_tag, topo_time)

      for afn in self.analysis_fns:
         (args, kws,_,_) = afn.signature
         fn = self._callables[afn.name]
         args = dict((key, info.specs[key]) for key in args)
         fn_kws = dict((key, info.specs[key]) for key in kws
                       if (key in info.specs))
         retval = fn(**dict(args, **fn_kws))
         self._accumulate_results(afn.name, retval)
         # If the analysis function fails let run_batch catch the Exception

         # The filename is unique as time increases during run_batch
         # Note that normalize_path prefix is set by run_batch
         if self._metadata.keys() != ['time'] or self._data:
            NumpyFile(directory= param.normalize_path.prefix,
                      hash_suffix = False).save(filename,
                                                metadata=self._metadata,
                                                **self._data)
      self._data = {}; self._metadata = {}


   def verify(self, specs, model_params):
      """
      The final check of argument specification before launch. Used to
      make sure the analysis functions have all the necessary
      arguments and warns about unused keys. If set to strict_verify,
      keywords are not allowed to be left unspecified.
      """

      (argset, kwargset) = self._argument_sets()

      known = ( argset | kwargset                # Analysisfn params...
                | set(model_params)              # Model params...
                | set(RunBatchCommand.params())  # Run batch params...
                | set(['times']))                # Common extras...
                                                 # Note: ignore list?
      missing, unknown = set(), set()
      for spec in specs:
         unknown = unknown | (set(spec)  - known)
         if self.strict_verify :
            missing = missing | ((argset | kwargset) - set(spec.keys()))
         else:
            missing = missing | (argset - set(spec.keys()))

         if not set(self.metadata).issubset(spec.keys()):
            raise Exception("Metadata keys not always available: %s"
                            % ', '.join(self.metadata))

      if unknown:
         print (("The following keys are not explicitly"
                 " consumed by RunBatchCommand: %s")
                % ', '.join('%r' % el for el in unknown))
      if not self.analysis_fns:
         raise Exception("Please specify at least one analysis function.")
      if missing:
         raise Exception("The following keys must be provided: %s"
                         % ", ".join('%r' % el for el in missing))


   def summary(self):
      """Summary of the analysis and the analysis functions used."""

      (args, kwargs) = self._argument_sets()
      print("Analysis functions:\n")
      for (ind, el) in enumerate(self.analysis_fns):
         print "   %d. %s%s" % (ind, el.name, inspect.formatargspec(*el.argspec))


   def _accumulate_results(self, fn_name, retval):
       """
       Accumulates the results of the analysis functions into the
       self._data and self._metadata attributes.
       """

       metadict = {}
       if isinstance(retval, dict):
           metadict = retval.pop('metadata', {})
           datadict = retval
           if not isinstance(metadict, dict):
               param.main.warning("Non-dictionary value of 'metadata'"
                                  " returned by %s. Ignoring." % fn_name)
       elif retval is not None:
           datadict = {fn_name:retval}
       else: return
       # Record returned data
       self._overwrite_warning(fn_name, self._data, datadict, 'data')
       self._data.update(datadict)
       # Record returned metadata
       self._overwrite_warning(fn_name, self._metadata, metadict, 'metadata')
       self._metadata.update(metadict)


   def _overwrite_warning(self, fn_name, current, additions, label):
      """Warn when overwriting previously returned or defined data."""

      intersection = set(current) & set(additions)
      overwrite_msg = "Analysis function %s overwriting existing %s keys: %s"
      if intersection:
         param.main.warning(overwrite_msg % (fn_name, label,
                                             ', '.join(intersection)))

   def _argument_sets(self):
      """
      Returns the full set of args and kwargs across all the
      registered analysis functions.
      """
      arglists = [afn.signature[0] for afn in self.analysis_fns]
      kwarglists = [afn.signature[1] for afn in self.analysis_fns]
      argset = set(arg for args in arglists for arg in args)
      kwargset = set(kw for kws in kwarglists for kw in kws)
      return (argset, kwargset)


   def _pprint(self, cycle=False, flat=False, annotate=False,
               onlychanged=True, level=1, tab = '   '):
      """Pretty print the Analysis in a declarative style."""

      path_str = '%spaths=%r\n' % ((level*tab), self.paths)
      path_str = '' if (onlychanged and not self.paths) else path_str
      fn_level = level + 1
      if len(self.analysis_fns) == 0:
         return "Analysis(%s)" % path_str
      elif len(self.analysis_fns) == 1:
         fn_list = repr(self.analysis_fns[0])
      else:
         listing = ['%r,' % afn for afn in self.analysis_fns[:-1]]
         listing.append(repr(self.analysis_fns[-1]))
         fn_list = [("\n"+tab*fn_level)+el for el in listing]
      return ("Analysis(\n%s" % path_str
              + (level*tab)
              + "analysis_fns=[%s]" % "".join(fn_list)
              + "\n"+(level*tab)+")")



class RunBatchCommand(TopoCommand):
   """
   Runs a custom analysis function of type Analysis with
   run_batch. This command is far more flexible for regular usage than
   TopoCommand as it allows you to build a run_batch analysis
   incrementally.
   """

   analysis = param.ClassSelector(class_=Analysis, allow_None=False)

   def __init__(self, tyfile, analysis, **kwargs):
      super(RunBatchCommand, self).__init__(tyfile=tyfile,
                                            analysis_fn = 'analysis_fn',
                                            analysis = analysis,
                                            do_format=False,
                                            **kwargs)
      self.pprint_args(['executable', 'tyfile', 'analysis'],
                       ['topo_switches', 'snapshot'])


   def get_model_params(self):
      """Obtains all the script parameters used in the model file."""
      # FIXME: This functionality cannot be properly integrated with
      # Topographica until models are defined as classes, allowing
      # parameters to be listed without needing to load the model.
      return self._model_params


   def __call__(self, spec=None, tid=None, info={}):
      """
      Generates the appropriate Topographica run_batch command to make
      use of the pickled RunBatchCommand object.
      """

      formatted_spec = dict((k, repr(v) if isinstance(v,str) else str(v))
                            for (k,v) in spec.items())
      kwarg_opts = self._run_batch_kwargs(formatted_spec, tid, info)
      allopts = dict(formatted_spec,**kwarg_opts) # Override spec values if
                                                  # mistakenly included.

      # Load and configure the Analysis object.
      prelude = ['from topo.misc.lancext import Analysis']
      config = (tid, info, spec)
      prelude += ["analysis_fn=Analysis.load(%r, %r, %r)" % config ]

      # Create the keyword representation to pass into run_batch
      keywords = ', '.join(['%s=%s' % (k,allopts[k]) for k in
                            sorted(kwarg_opts.keys())
                            +sorted(formatted_spec.keys())])
      run_batch_list = prelude + ["run_batch(%s,%s)"
                                  % (repr(self.tyfile), keywords)]
      topo_args = self._topo_args(['-a'])
      return  [self.executable] + topo_args + ['-c',  '; '.join(run_batch_list)]


   def verify(self, args):
      """
      Check that the supplied arguments make sense given the specified
      analysis.
      """
      # FIXME: Disabled until models allow inspection of their
      # parameters without needing to be fully loaded into memory.
      if hasattr(self, '_model_params'):
         return self.analysis.verify(args.specs, self.get_model_params())


   def finalize(self, info):
      """Pickle the analysis before launch."""
      pkl_path = self.analysis.pickle_path(info)
      with open(pkl_path,'wb') as pkl:
         pickle.dump(self.analysis, pkl)


   def summary(self):
      print("Command executable: %s" % self.executable)
      self.analysis.summary()
