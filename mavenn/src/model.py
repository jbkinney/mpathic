from mavenn.src.error_handling import handle_errors, check
from mavenn.src.UI import GlobalEpistasisModel, NoiseAgnosticModel
from mavenn.src.utils import fix_gauge_additive_model, fix_gauge_neighbor_model, fix_gauge_pairwise_model
from mavenn.src.utils import onehot_encode_array, \
    _generate_nbr_features_from_sequences, _generate_all_pair_features_from_sequences
from mavenn.src.likelihood_layers import *
from mavenn.src.utils import fixDiffeomorphicMode
from mavenn.src.utils import GaussianNoiseModel, CauchyNoiseModel, SkewedTNoiseModel
from mavenn.src.utils import mi_continuous

import tensorflow as tf
import tensorflow.keras
from tensorflow.keras.optimizers import Adam
from tensorflow.keras.optimizers import *
from tensorflow.keras.models import Model as kerasFunctionalModel # to distinguish from class name
from tensorflow.keras.layers import Dense, Activation, Input, Lambda, Concatenate
from tensorflow.keras.constraints import non_neg as nonneg
import tensorflow.keras.backend as K

import pandas as pd
import numpy as np


@handle_errors
class Model:

    """
    Mavenn's model class that lets the user choose either
    global epistasis regression or noise agnostic regression

    If regerssion_type == 'NA', than ge_* parameters are not used.


    attributes
    ----------

    x: (array-like)
        Input pandas DataFrame containing sequences. x are
        DNA, RNA, or protein sequences to be regressed over

    y: (array-like)
        y represents counts in bins, or continuous measurement values
        corresponding to the sequences x

    alphabet: (str)
        Specifies the type of input sequences. Three possible choices
        allowed: ['dna','rna','protein'].

    regression_type: (str)
        variable that choose type of regression, valid options
        include 'GE', 'NA'

    gpmap_type: (str)
        Specifies the type of G-P model the user wants to infer.
        Three possible choices allowed: ['additive','neighbor','pairwise']

    ge_nonlinearity_monotonic: (boolean)
        Whether to use a monotonicity constraint in GE regression.
        This variable has no effect for NA regression.

    ge_nonlinearity_hidden_nodes:
        Number of hidden nodes (i.e. sigmoidal contributions) to use in the
        definition of the GE nonlinearity.

    ge_noise_model_type: (str)
        Specifies the type of noise model the user wants to infer.
        The possible choices allowed: ['Gaussian','Cauchy','SkewedT']

    ge_heteroskedasticity_order: (int)
        Order of the exponentiated polynomials used to make noise model parameters
        dependent on y_hat, and thus render the noise model heteroskedastic. Set
        to zero for a homoskedastic noise model. (Only used for GE regression).

    na_hidden_nodes:
        Number of hidden nodes (i.e. sigmoidal contributions) to use in the
        definition of the NA measurement process.

    theta_regularization: (float >= 0)
        Regularization strength for G-P map parameters $\theta$.

    eta_regularization: (float >= 0)
        Regularization strength for measurement process parameters $\eta$.

    ohe_batch_size: (int)
        Integer specifying how many sequences to one-hot encode at a time.
        The larger this number number, the quicker the encoding will happen,
        but this may also take up a lot of memory and throw an exception
        if its too large. Currently for additive models only.

    """

    def __init__(self,
                 x,
                 y,
                 alphabet,
                 regression_type,
                 gpmap_type='additive',
                 ge_nonlinearity_monotonic=True,
                 ge_nonlinearity_hidden_nodes=50,
                 ge_noise_model_type='Gaussian',
                 ge_heteroskedasticity_order=2,
                 na_hidden_nodes=50,
                 theta_regularization=0.01,
                 eta_regularization=0.01,
                 ohe_batch_size=50000):

        # set class attributes
        self.x, self.y = x, y
        self.alphabet = alphabet
        self.regression_type = regression_type
        self.gpmap_type = gpmap_type
        self.ge_nonlinearity_monotonic = ge_nonlinearity_monotonic
        self.ge_nonlinearity_hidden_nodes = ge_nonlinearity_hidden_nodes
        self.ge_noise_model_type = ge_noise_model_type
        self.ge_heteroskedasticity_order = ge_heteroskedasticity_order
        self.na_hidden_nodes = na_hidden_nodes
        self.theta_regularization = theta_regularization
        self.eta_regularization = eta_regularization
        self.ohe_batch_size = ohe_batch_size

        # represents GE or NA model object, depending which is chosen.
        # attribute value is set below
        self.model = None

        # check that regression_type is valid
        check(self.regression_type in {'NA', 'GE'},
              'regression_type = %s; must be "NA", or  "GE"' %
              self.gpmap_type)

        # choose model based on regression_type
        if regression_type == 'GE':

            self.model = GlobalEpistasisModel(X=self.x,
                                              y=self.y,
                                              gpmap_type=self.gpmap_type,
                                              ge_nonlinearity_monotonic=self.ge_nonlinearity_monotonic,
                                              alphabet=self.alphabet,
                                              ohe_batch_size=self.ohe_batch_size,
                                              ge_heteroskedasticity_order=self.ge_heteroskedasticity_order)

            self.define_model = self.model.define_model(ge_noise_model_type=self.ge_noise_model_type,
                                                        ge_nonlinearity_hidden_nodes=
                                                        self.ge_nonlinearity_hidden_nodes)

        elif regression_type == 'NA':
            self.model = NoiseAgnosticModel(x=self.x,
                                            y=self.y,
                                            alphabet=self.alphabet,
                                            gpmap_type=self.gpmap_type,
                                            ohe_batch_size=self.ohe_batch_size)

            self.define_model = self.model.define_model(na_hidden_nodes = self.na_hidden_nodes)

    @handle_errors
    def gauge_fix_model_multiple_replicates(self):

        """
        Method that gauge fixes the model (x_to_phi+measurement).


        parameters
        ----------
        None

        returns
        -------
        None

        """

        # TODO disable this method if user uses custom architecture

        # Helper variables used for gauge fixing x_to_phi trait parameters theta below.
        sequence_length = len(self.model.x_train[0])
        alphabetSize = len(self.model.characters)

        # Non-gauge fixed theta
        theta_all = self.model.model.layers[2].get_weights()[0]    # E.g., could be theta_additive + theta_pairwise
        theta_nought = self.model.model.layers[2].get_weights()[1]
        theta = np.hstack((theta_nought, theta_all.ravel()))

        # The following conditionals gauge fix the x_to_phi parameters depending of the value of x_to_phi
        if self.gpmap_type == 'additive':

            # compute gauge-fixed, additive model theta
            theta_gf = fix_gauge_additive_model(sequence_length, alphabetSize, theta)

        elif self.gpmap_type == 'neighbor':

            # compute gauge-fixed, neighbor model theta
            theta_gf = fix_gauge_neighbor_model(sequence_length, alphabetSize, theta)

        elif self.gpmap_type == 'pairwise':

            # compute gauge-fixed, pairwise model theta
            theta_gf = fix_gauge_pairwise_model(sequence_length, alphabetSize, theta)

        # The following variable unfixed_gpmap is a tf.keras backend function
        # which computes the non-gauge fixed value of the hidden node phi for a given input
        # this is  used to compute diffeomorphic scaling factor.
        unfixed_gpmap = K.function([self.model.model.layers[1].input], [self.model.model.layers[2].output])

        # compute unfixed phi using the function unfixed_gpmap with training sequences.
        unfixed_phi = unfixed_gpmap([self.model.input_seqs_ohe])

        # Compute diffeomorphic scaling factor which is used to rescale the parameters theta
        diffeomorphic_std = np.sqrt(np.var(unfixed_phi[0]))
        diffeomorphic_mean = np.mean(unfixed_phi[0])

        # Default neural network weights that are non gauge fixed.
        # This will be used for updating the weights of the measurement
        # network after the gauge fixed neural network is define below.
        temp_weights = [layer.get_weights() for layer in self.model.model.layers]

        # define gauge fixed model

        if self.regression_type == 'GE':

            if len(self.model.y_train.shape) == 1:
                number_of_replicate_targets = 1
            else:
                number_of_replicate_targets = min(self.model.y_train.shape)

            print('number of y nodes to add: ', number_of_replicate_targets)
            # create input layer with nodes allowing sequence to be input and also
            # target labels to be input, together.
            #number_input_layer_nodes = len(self.input_seqs_ohe[0])+self.y_train.shape[0]
            number_input_layer_nodes = len(self.model.input_seqs_ohe[0]) + number_of_replicate_targets
            inputTensor = Input((number_input_layer_nodes,), name='Sequence_labels_input')

            sequence_input = Lambda(lambda x: x[:, 0:len(self.model.input_seqs_ohe[0])],
                                    output_shape=((len(self.model.input_seqs_ohe[0]),)), name='Sequence_only')(inputTensor)

            replicates_input = []

            #number_of_replicate_targets = self.y_train.shape[0]

            for replicate_layer_index in range(number_of_replicate_targets):

                # build up lambda layers, on step at a time, which will be
                # fed to each of the measurement blocks
                print(replicate_layer_index, replicate_layer_index + 1)

                temp_replicate_layer = Lambda(lambda x:
                                              x[:, len(self.model.input_seqs_ohe[0])+replicate_layer_index:
                                              len(self.model.input_seqs_ohe[0]) + replicate_layer_index + 1],
                                              output_shape=((1,)), trainable=False,
                                              name='Labels_input_'+str(replicate_layer_index))(inputTensor)

                replicates_input.append(temp_replicate_layer)

            # labels_input_rep1 = Lambda(lambda x: x[:, len(self.input_seqs_ohe[0]):len(self.input_seqs_ohe[0]) + 1],
            #                       output_shape=((1, )), trainable=False, name='Labels_input_1')(inputTensor)
            #
            # labels_input_rep2 = Lambda(lambda x: x[:, len(self.input_seqs_ohe[0])+1:len(self.input_seqs_ohe[0]) + 2],
            #                            output_shape=((1,)), trainable=False, name='Labels_input_2')(inputTensor)

            # sequence to latent phenotype
            #phi = Dense(1, name='phi')(sequence_input)

        elif self.regression_type == 'NA':

            number_input_layer_nodes = len(self.model.input_seqs_ohe[0])+self.model.y_train.shape[1]
            inputTensor = Input((number_input_layer_nodes,), name='Sequence_labels_input')

            sequence_input = Lambda(lambda x: x[:, 0:len(self.model.input_seqs_ohe[0])],
                                    output_shape=((len(self.model.input_seqs_ohe[0]),)), name='Sequence_only')(inputTensor)
            labels_input = Lambda(lambda x: x[:, len(self.model.input_seqs_ohe[0]):len(self.model.input_seqs_ohe[0]) + self.model.y_train.shape[1]],
                                  output_shape=((1,)), trainable=False, name='Labels_input')(inputTensor)

        # same phi as before
        phi = Dense(1, name='phiPrime')(sequence_input)
        # fix diffeomorphic scale
        phi_scaled = fixDiffeomorphicMode()(phi)
        phiOld = Dense(1, name='phi')(phi_scaled)

        # implement monotonicity constraints if GE regression
        if self.regression_type == 'GE':

            if self.ge_nonlinearity_monotonic:

                # phi feeds into each of the replicate intermediate layers
                intermediate_layers = []
                for intermediate_index in range(number_of_replicate_targets):

                    temp_intermediate_layer = Dense(self.ge_nonlinearity_hidden_nodes,
                                                    activation='sigmoid',
                                                    kernel_constraint=nonneg(),
                                                    name='intermediate_bbox_'+str(intermediate_index))(phiOld)

                    intermediate_layers.append(temp_intermediate_layer)

                yhat_layers = []
                for yhat_index in range(number_of_replicate_targets):

                    temp_yhat_layer = Dense(1, kernel_constraint=nonneg(),
                                            name='y_hat_rep_'+str(yhat_index))(intermediate_layers[yhat_index])
                    yhat_layers.append(temp_yhat_layer)

                # intermediateTensor = Dense(self.num_nodes_hidden_measurement_layer, activation='sigmoid',
                #                            kernel_constraint=nonneg())(phiOld)

                # y_hat = Dense(1, kernel_constraint=nonneg())(intermediateTensor)

                # concatenateLayer = Concatenate(name='yhat_and_y_to_ll')([y_hat, labels_input])

                concatenateLayer_rep_input = []

                for concat_index in range(number_of_replicate_targets):

                    temp_concat = Concatenate(name='yhat_and_rep_'+str(concat_index))\
                        ([yhat_layers[concat_index], replicates_input[concat_index]])

                    concatenateLayer_rep_input.append(temp_concat)

                likelihoodClass = globals()[self.ge_noise_model_type + 'LikelihoodLayer']

                #ll_rep1 = likelihoodClass(self.polynomial_order_ll)(concatenateLayer_rep1)
                #ll_rep2 = likelihoodClass(self.polynomial_order_ll)(concatenateLayer_rep2)

                ll_rep_layers = []
                for ll_index in range(number_of_replicate_targets):
                    temp_ll_layer = likelihoodClass(self.ge_heteroskedasticity_order)(concatenateLayer_rep_input[ll_index])
                    ll_rep_layers.append(temp_ll_layer)


                #outputTensor = [ll_rep1, ll_rep2]
                outputTensor = ll_rep_layers

                # dynamic likelihood class instantiation by the globals dictionary
                # manual instantiation can be done as follows:
                # outputTensor = GaussianLikelihoodLayer()(concatenateLayer)

                # likelihoodClass = globals()[self.noise_model + 'LikelihoodLayer']
                # outputTensor = likelihoodClass(self.polynomial_order_ll)(concatenateLayer)

            else:
                intermediateTensor = Dense(self.ge_nonlinearity_hidden_nodes, activation='sigmoid')(phiOld)
                y_hat = Dense(1)(intermediateTensor)

                concatenateLayer = Concatenate(name='yhat_and_y_to_ll')([y_hat, labels_input])

                likelihoodClass = globals()[self.ge_noise_model_type + 'LikelihoodLayer']
                outputTensor = likelihoodClass(self.ge_heteroskedasticity_order)(concatenateLayer)

        elif self.regression_type == 'NA':

            #intermediateTensor = Dense(self.num_nodes_hidden_measurement_layer, activation='sigmoid')(phi)
            #outputTensor = Dense(np.shape(self.model.y_train[0])[0], activation='softmax')(intermediateTensor)

            intermediateTensor = Dense(self.ge_nonlinearity_hidden_nodes, activation='sigmoid')(phiOld)
            yhat = Dense(np.shape(self.model.y_train[0])[0], name='yhat', activation='softmax')(intermediateTensor)

            concatenateLayer = Concatenate(name='yhat_and_y_to_ll')([yhat, labels_input])
            outputTensor = NALikelihoodLayer(number_bins=np.shape(self.model.y_train[0])[0])(concatenateLayer)


        # create the gauge-fixed model:
        model_gf = kerasFunctionalModel(inputTensor, outputTensor)

        # set new model theta weights
        theta_nought_gf = theta_gf[0]
        model_gf.layers[2].set_weights([theta_gf[1:].reshape(-1, 1), np.array([theta_nought_gf])])

        # update weights as sigma*phi+mean, which ensures predictions (y_hat) don't change from
        # the diffeomorphic scaling.
        model_gf.layers[4].set_weights([np.array([[diffeomorphic_std]]), np.array([diffeomorphic_mean])])

        for layer_index in range(5, len(model_gf.layers)):
            model_gf.layers[layer_index].set_weights(temp_weights[layer_index-2])

        # Update default neural network model with gauge-fixed model
        self.model.model = model_gf

        # The theta_gf attribute now contains gauge fixed parameters, and
        # can be obtained in raw form by accessing this attribute or can be
        # obtained a readable format by using the method return_theta
        self.model.theta_gf = theta_gf.reshape(len(theta_gf), 1)

    @handle_errors
    def fix_gauge(self, gauge="hierarchichal", wt_sequence=None):
        """
        Gauge-fixes the G-P map parameters $\theta$

        parameters
        ----------
        gauge: (string)
            Gauge to use. Options are "hierarchichal" or "wild-type".

        wt_sequence: (string)
            Sequence to use when adopting the wild-type gauge.

        returns
        -------
        None
        """
        # TODO: Fill out this function. If user calls this method and wants
        # to switch to WT gauge, if parameters are HA, switch parameters to WT, and vice versa
        self.gauge = gauge
        self.wt_sequence = wt_sequence
        pass

    # TODO: put underscore in front on function name
    @handle_errors
    def gauge_fix_model(self):

        """
        Method that gauge fixes the entire model (x_to_phi+measurement).

        parameters
        ----------
        None

        returns
        -------
        None

        """

        # Helper variables used for gauge fixing x_to_phi trait parameters theta below.
        sequence_length = len(self.model.x_train[0])
        alphabetSize = len(self.model.characters)

        # Non-gauge fixed theta
        theta_all = self.model.model.layers[2].get_weights()[0]    # E.g., could be theta_additive + theta_pairwise
        theta_nought = self.model.model.layers[2].get_weights()[1]
        theta = np.hstack((theta_nought, theta_all.ravel()))

        # The following conditionals gauge fix the x_to_phi parameters depending of the value of x_to_phi
        if self.gpmap_type == 'additive':

            # compute gauge-fixed, additive model theta
            theta_gf = fix_gauge_additive_model(sequence_length, alphabetSize, theta)

        elif self.gpmap_type == 'neighbor':

            # compute gauge-fixed, neighbor model theta
            theta_gf = fix_gauge_neighbor_model(sequence_length, alphabetSize, theta)

        elif self.gpmap_type == 'pairwise':

            # compute gauge-fixed, pairwise model theta
            theta_gf = fix_gauge_pairwise_model(sequence_length, alphabetSize, theta)

        # The following variable unfixed_gpmap is a tf.keras backend function
        # which computes the non-gauge fixed value of the hidden node phi for a given input
        # this is  used to compute diffeomorphic scaling factor.
        unfixed_gpmap = K.function([self.model.model.layers[1].input], [self.model.model.layers[2].output])

        # compute unfixed phi using the function unfixed_gpmap with training sequences.
        unfixed_phi = unfixed_gpmap([self.model.input_seqs_ohe])

        # Compute diffeomorphic scaling factor which is used to rescale the parameters theta
        diffeomorphic_std = np.sqrt(np.var(unfixed_phi[0]))
        diffeomorphic_mean = np.mean(unfixed_phi[0])

        # Default neural network weights that are non gauge fixed.
        # This will be used for updating the weights of the measurement
        # network after the gauge fixed neural network is define below.
        temp_weights = [layer.get_weights() for layer in self.model.model.layers]

        # define gauge fixed model

        if self.regression_type == 'GE':

            number_input_layer_nodes = len(self.model.input_seqs_ohe[0]) + 1     # the plus 1 indicates the node for y
            inputTensor = Input((number_input_layer_nodes,), name='Sequence_labels_input')

            sequence_input = Lambda(lambda x: x[:, 0:len(self.model.input_seqs_ohe[0])],
                                    output_shape=((len(self.model.input_seqs_ohe[0]),)))(inputTensor)

            labels_input = Lambda(
                lambda x: x[:, len(self.model.input_seqs_ohe[0]):len(self.model.input_seqs_ohe[0]) + 1],
                output_shape=((1,)), trainable=False)(inputTensor)

        elif self.regression_type == 'NA':

            number_input_layer_nodes = len(self.model.input_seqs_ohe[0])+self.model.y_train.shape[1]
            inputTensor = Input((number_input_layer_nodes,), name='Sequence_labels_input')

            sequence_input = Lambda(lambda x: x[:, 0:len(self.model.input_seqs_ohe[0])],
                                    output_shape=((len(self.model.input_seqs_ohe[0]),)), name='Sequence_only')(inputTensor)
            labels_input = Lambda(lambda x: x[:, len(self.model.input_seqs_ohe[0]):len(self.model.input_seqs_ohe[0]) + self.model.y_train.shape[1]],
                                  output_shape=((1,)), trainable=False, name='Labels_input')(inputTensor)



        # same phi as before
        phi = Dense(1, name='phiPrime')(sequence_input)
        # fix diffeomorphic scale
        phi_scaled = fixDiffeomorphicMode()(phi)
        phiOld = Dense(1, name='phi')(phi_scaled)

        # implement monotonicity constraints if GE regression
        if self.regression_type == 'GE':

            if self.ge_nonlinearity_monotonic:

                intermediateTensor = Dense(self.ge_nonlinearity_hidden_nodes, activation='sigmoid',
                                           kernel_constraint=nonneg())(phiOld)
                y_hat = Dense(1, kernel_constraint=nonneg())(intermediateTensor)

                concatenateLayer = Concatenate(name='yhat_and_y_to_ll')([y_hat, labels_input])

                # dynamic likelihood class instantiation by the globals dictionary
                # manual instantiation can be done as follows:
                # outputTensor = GaussianLikelihoodLayer()(concatenateLayer)

                likelihoodClass = globals()[self.ge_noise_model_type + 'LikelihoodLayer']
                outputTensor = likelihoodClass(self.ge_heteroskedasticity_order)(concatenateLayer)

            else:
                intermediateTensor = Dense(self.ge_nonlinearity_hidden_nodes, activation='sigmoid')(phiOld)
                y_hat = Dense(1)(intermediateTensor)

                concatenateLayer = Concatenate(name='yhat_and_y_to_ll')([y_hat, labels_input])

                likelihoodClass = globals()[self.ge_noise_model_type + 'LikelihoodLayer']
                outputTensor = likelihoodClass(self.ge_heteroskedasticity_order)(concatenateLayer)

        elif self.regression_type == 'NA':

            #intermediateTensor = Dense(self.num_nodes_hidden_measurement_layer, activation='sigmoid')(phi)
            #outputTensor = Dense(np.shape(self.model.y_train[0])[0], activation='softmax')(intermediateTensor)

            intermediateTensor = Dense(self.na_hidden_nodes, activation='sigmoid')(phiOld)
            yhat = Dense(np.shape(self.model.y_train[0])[0], name='yhat', activation='softmax')(intermediateTensor)

            concatenateLayer = Concatenate(name='yhat_and_y_to_ll')([yhat, labels_input])
            outputTensor = NALikelihoodLayer(number_bins=np.shape(self.model.y_train[0])[0])(concatenateLayer)


        # create the gauge-fixed model:
        model_gf = kerasFunctionalModel(inputTensor, outputTensor)

        # set new model theta weights
        theta_nought_gf = theta_gf[0]
        model_gf.layers[2].set_weights([theta_gf[1:].reshape(-1, 1), np.array([theta_nought_gf])])

        # update weights as sigma*phi+mean, which ensures predictions (y_hat) don't change from
        # the diffeomorphic scaling.
        model_gf.layers[4].set_weights([np.array([[diffeomorphic_std]]), np.array([diffeomorphic_mean])])

        for layer_index in range(5, len(model_gf.layers)):
            model_gf.layers[layer_index].set_weights(temp_weights[layer_index-2])

        # Update default neural network model with gauge-fixed model
        self.model.model = model_gf

        # The theta_gf attribute now contains gauge fixed parameters, and
        # can be obtained in raw form by accessing this attribute or can be
        # obtained a readable format by using the method return_theta
        self.model.theta_gf = theta_gf.reshape(len(theta_gf), 1)

    @handle_errors
    def fit(self,
            epochs=50,
            learning_rate=0.005,
            validation_split=0.2,
            verbose=1,
            early_stopping=True,
            early_stopping_patience=20,
            callbacks=[],
            optimizer=Adam,
            optimizer_kwargs={},
            fit_kwargs={},
            compile_kwargs={}):

        """

        Infers parameters, from data, for both the G-P map and the measurement process.

        parameters
        ----------
        epochs: (int>0)
            Maximum number of epochs to complete during training.

        learning_rate: (float > 0)
            Learning rate that will get passed to the optimizer.

        validation_split: (float in [0,1])
            Fraction of training data to be split into a validation set.

        verbose: (0 or 1, or boolean)
            Will show training progress if 1 or True, nothing if 0 or False.

        early_stopping: (bool)
            specifies whether to use early stopping or not

        early_stopping_patience: (int)
            If using early stopping, specifies the number of epochs to wait
            after a new optimum is identified.


        callbacks: (list)
            List of tf.keras.callbacks.Callback instances.

        optimizer: (string or tf.keras.optimizers.Optimizer instance)
            Optimizer to use. Name of a TensorFlow optimizer. Valid string options are:
            ['SGD', 'RMSprop', 'Adam', 'Adadelta', 'Adagrad', 'Adamax', 'Nadam', 'Ftrl']

        optimizer_kwargs: (dict)
            Additional keyword arguments to pass to the constructor of the
            tf.keras.optimizers.Optimizer class.

        fit_kwargs: (dict)
            Additional keyword arguments to pass to tf.keras.model.fit().

        compile_kwargs: (dict):
            Additional keyword arguments to pass to tf.keras.model.compile().

        returns
        -------
        history: (tf.keras.callbacks.History object)
            Standard TensorFlow record of the optimization session.

        """

        self.learning_rate = learning_rate
        self._compile_model(optimizer=optimizer,
                           lr=self.learning_rate,
                           **optimizer_kwargs,
                           **compile_kwargs)

        if early_stopping:
            callbacks += [tensorflow.keras.callbacks.EarlyStopping(monitor='val_loss',
                                                                  mode='auto',
                                                                  patience=early_stopping_patience)]

        # OHE training sequences with y appended to facilitate the calculation of likelihood.
        train_sequences = []

        # To each sequence in the training set, its target value is appended
        # to its one-hot encoded form, which gets passed to fit.
        for n in range(len(self.model.input_seqs_ohe)):
            temp = self.model.input_seqs_ohe[n].ravel()
            temp = np.append(temp, self.model.y_train[n])
            train_sequences.append(temp)

        train_sequences = np.array(train_sequences)

        history = self.model.model.fit(train_sequences,
                                       self.model.y_train,
                                       validation_split=validation_split,
                                       epochs=epochs,
                                       verbose=verbose,
                                       callbacks=callbacks,
                                       **fit_kwargs,
                                       )

        # gauge fix model after fitting
        self.gauge_fix_model()

        # update history attribute
        self.model.history = history
        return history

    @handle_errors
    def phi_to_yhat(self,
                    phi):

        """
        Evaluate the GE nonlinearity at specified values of phi (the latent phenotype).

        parameters
        ----------

        phi: (array-like)
            Latent phenotype values at which to evaluate the GE nonlinearity

        returns
        -------
        y_hat: (array-like)
            GE nonlinearity evaluated on phi values

        """

        check(self.regression_type == 'GE', 'regression type must be "GE" for this function ')

        y_hat = self.model.phi_to_yhat(phi)

        return y_hat

    @handle_errors
    def get_gpmap_parameters(self):

        """
        Returns gauge-fixed parameters for the G-P map as a pandas dataframe.
        The returned dataframe has two columns: name and values.
        The format of each parameter name is of the following form:

        constant parameters: "theta_0"
        additive parameters: "theta_1:A"
        pairwise parameters: "theta_1:A,5:G"

        returns
        -------
        theta_df: (pd.DataFrame)
            Gauge-fixed G-P map parameters, formatted as a dataframe.
        """

        # temp variable to store characters.
        chars = self.model.characters

        # position and character indices
        char_indices = list(range(len(chars)))
        pos_indices = list(range(len(self.model.x_train[0])))

        # list that will contain parameter names
        names = []

        # list that will contain parameter values corresponding to names
        values = []

        # These parameters are gauge fixed are the model has been fit.
        if self.gpmap_type == 'additive':

            # get constant term.
            theta_0 = self.model.theta_gf[0][0]

            # add it to the lists that will get returned.
            names.append('theta_0')
            values.append(theta_0)

            reshaped_theta = self.model.theta_gf[1:].reshape(len(self.model.x_train[0]), len(chars))
            for position in pos_indices:
                for char in char_indices:
                    names.append('theta_' + str(position) + ':' + chars[char])
                    values.append(reshaped_theta[position][char])

        elif self.gpmap_type == 'neighbor':

            # define helper variables
            sequenceLength = len(self.model.x_train[0])
            num_possible_pairs = int((sequenceLength * (sequenceLength - 1)) / 2)

            # get constant term.
            theta_0 = self.model.theta_gf[0][0]

            # add it to the lists that will get returned.
            names.append('theta_0')
            values.append(theta_0)

            # get additive terms, starting from 1 because 0 represents constant term
            reshaped_theta = self.model.theta_gf[1:sequenceLength*len(self.model.characters)+1].\
                reshape(len(self.model.x_train[0]), len(chars))

            for position in pos_indices:
                for char in char_indices:
                    names.append('theta_' + str(position) + ':' + chars[char])
                    values.append(reshaped_theta[position][char])

            reshaped_theta = self.model.theta_gf[sequenceLength*len(self.model.characters)+1:]\
                .reshape(len(self.model.x_train[0]) - 1, len(chars), len(chars))

            # get parameters in tidy format
            for pos1 in pos_indices[:-1]:
                for char1 in char_indices:
                    for char2 in char_indices:
                        value = reshaped_theta[pos1][char1][char2]
                        name = f'theta_{pos1}:{chars[char1]}{chars[char2]}'
                        names.append(name)
                        values.append(value)

        elif self.gpmap_type == 'pairwise':

            # define helper variables
            sequenceLength = len(self.model.x_train[0])
            num_possible_pairs = int((sequenceLength * (sequenceLength - 1)) / 2)

            # get constant term.
            theta_0 = self.model.theta_gf[0][0]

            # add it to the lists that will get returned.
            names.append('theta_0')
            values.append(theta_0)

            # get additive terms, starting from 1 because 0 represents constant term
            reshaped_theta = self.model.theta_gf[1:sequenceLength*len(self.model.characters)+1].\
                reshape(len(self.model.x_train[0]), len(chars))

            for position in pos_indices:
                for char in char_indices:
                    names.append('theta_' + str(position) + ':' + chars[char])
                    values.append(reshaped_theta[position][char])


            # get pairwise terms
            # reshape to num_possible_pairs by len(chars) by len(chars) array
            reshaped_theta = self.model.theta_gf[sequenceLength*len(self.model.characters)+1:].\
                reshape(num_possible_pairs, len(chars), len(chars))

            pos_pair_num = 0
            for pos1 in pos_indices:
                for pos2 in pos_indices[pos1+1:]:
                    for char1 in char_indices:
                        for char2 in char_indices:
                            value = reshaped_theta[pos_pair_num][char1][char2]
                            name = f'theta_{pos1}:{chars[char1]},{pos2}:{chars[char2]}'
                            names.append(name)
                            values.append(value)
                    pos_pair_num += 1

        theta_df = pd.DataFrame(
            {'name': names,
             'value': values
             })

        return theta_df

    # TODO: Rename to na_p_of_ally_given_phi
    @handle_errors
    def na_noisemodel(self,
                      phi):

        """
        Evaluate the NA measurement process at specified values of phi (the latent phenotype).

        parameters
        ----------

        phi: (array-like)
            Latent phenotype values at which to evaluate the measurement process.

        returns
        -------
        p_y_given_phi: (array-like)
            Measurement process p(y|phi) for all possible values of y. Is of size
            MxY where M=len(phi) and Y is the number of possible y values.

        """

        check(self.regression_type == 'NA', 'regression type must be "NA" for this function ')

        pi = self.model.noise_model(phi)

        return pi

    @handle_errors
    def get_nn(self):

        """
        Returns the tf neural network used to represent the inferred model.
        """

        return self.model.model

    # TODO: Make internal
    @handle_errors
    def _compile_model(self,
                      optimizer=Adam,
                      lr=0.005,
                      optimizer_kwargs={},
                      compile_kwargs={}):
        """
        This method will compile the model created in the constructor. The loss used will be
        log_poisson_loss for NA regression, or mean_squared_error for GE regression

        parameters
        ----------

        optimizer: (str)
            Specifies which optimizers to use during training. See
            'https://www.tensorflow.org/api_docs/python/tf/keras/optimizers',
            for a all available optimizers


        lr: (float)
            Learning rate of the optimizer.

        returns
        -------
        None

        """

        if self.regression_type == 'GE':

            # Note: this loss just returns the computed
            # Likelihood in the custom likelihood layer
            def likelihood_loss(y_true, y_pred):

                return K.sum(y_pred)

            self.model.model.compile(loss=likelihood_loss,
                                     optimizer=optimizer(lr=lr, **optimizer_kwargs),
                                     **compile_kwargs)

        elif self.regression_type == 'NA':


            def likelihood_loss(y_true, y_pred):
                return y_pred

            #self.model.model.compile(loss=tf.nn.log_poisson_loss,
            self.model.model.compile(loss=likelihood_loss,
                                     optimizer=optimizer(lr=lr, **optimizer_kwargs),
                                     **compile_kwargs)

    @handle_errors
    def x_to_phi(self,
                 x):
        """

        Evaluates the latent phenotype phi on input sequences.

        parameters
        ----------
        x: (array-like of str)
            Sequence inputs representing DNA, RNA, or protein (whichever
            type of sequence the model was trained on). Input can must be
            an array of str, all the proper length.

        returns
        -------
        phi: (array-like of float)
            Array of latent phenotype values.

        """
        if self.gpmap_type == 'additive':
            # one-hot encode sequences in batches in a vectorized way
            seqs_ohe = onehot_encode_array(x, self.model.characters)

        elif self.gpmap_type == 'neighbor':
            # Generate additive one-hot encoding.
            X_test_additive = onehot_encode_array(x, self.model.characters, self.ohe_batch_size)

            # Generate neighbor one-hot encoding.
            X_test_neighbor = _generate_nbr_features_from_sequences(x, self.alphabet)

            # Append additive and neighbor features together.
            seqs_ohe = np.hstack((X_test_additive, X_test_neighbor))

        elif self.gpmap_type == 'pairwise':
            # Generate additive one-hot encoding.
            X_test_additive = onehot_encode_array(x, self.model.characters, self.ohe_batch_size)

            # Generate pairwise one-hot encoding.
            X_test_pairwise = _generate_all_pair_features_from_sequences(x, self.alphabet)

            # Append additive and pairwise features together.
            seqs_ohe = np.hstack((X_test_additive, X_test_pairwise))

        # Form tf.keras function that will evaluate the value of gauge fixed latent phenotype
        gpmap_function = K.function([self.model.model.layers[1].input], [self.model.model.layers[3].output])

        # Compute latent phenotype values
        phi = gpmap_function([seqs_ohe])

        # Remove extra dimension tf adds
        phi = phi[0].ravel().copy()

        # Return latent phenotype values
        return phi

    # TODO: rename these functions
    # self.x_to_phi -> self.x_to_phi
    # self.measurement_process -> self.p_y_given_phi  # Implement this
    # self.likelihood -> self.p_y_given_x             # Write this
    # self.x_to_yhat -> self.x_to_yhat
    # self.phi_to_yhat -> self.phi_to_yhat
    # self.ge_noise_model -> self.p_y_given_yhat

    @handle_errors
    def x_to_yhat(self,
                  x):

        """
        Make predictions for arbitrary input sequences. Note that this returns the output of
        the measurement process, not the latent phenotype.

        parameters
        ----------
        x: (array-like)
            Sequence data on which to make predictions.

        returns
        -------
        predictions: (array-like)
            An array of predictions for GE regression.
        """

        check(self.regression_type == 'GE', 'Regression type must be GE for this function.')

        yhat = self.phi_to_yhat(self.x_to_phi(x))
        #yhat = yhat[0].ravel().copy()
        return yhat


    def I_predictive(self,
                     x,
                     y,
                     y_format=None,
                     knn=5,
                     uncertainty=False,
                     num_subsamples=25,
                     use_LNC=False,
                     alpha_LNC=.5,
                     verbose=False):
        """
        Estimate the predictive information I[y;phi] on supplied data.

        parameters
        ----------

        x: (array-like of strings)
            Array of sequences for which to comptue phi values.

        y: (array-like of floats)
            Array of measurements y to use when computing I[y;phi].
            If measurements are continuous, y must be the same shape as
            x. If measurements are discrete, y can have two formats.
            If y_format="list", y should be a list of discrete values,
            one for each x. If y_format="matrix", y should be a
            MxY matrix, where M=len(x) and Y is the number of possible
            values for Y.

        y_format: (string)
            Must be either "list" or "matrix": "list" represents a list
            of discrete y values, while "matrix" represents a matrix of
            counts in bins

        knn: (int>0)
            Number of nearest neighbors to use in the KSG estimator.

        uncertainty: (bool)
            Whether to estimate the uncertainty of the MI estimate.
            Substantially increases runtime if True.

        num_subsamples: (int > 0)
            Number of subsamples to use if estimating uncertainty.

        use_LNC: (bool)
            Whether to compute the Local Nonuniform Correction
            (LNC) using the method of Gao et al., 2015.
            Substantially increases runtime if True. Only used for
            continuous y values.

        alpha_LNC: (float in (0,1))
            Value of alpha to use when computing LNC.
            See Gao et al., 2015 for details. Only used for
            continuous y values.

        verbose: (bool)
            Whether to print results and execution time.

        returns
        -------

        I: (float)
            Mutual information estimate in bits

        dI: (float >= 0)
            Uncertainty estimate in bits. Zero if uncertainty=False is set.
            Not returned if uncertainty=False is set.

        """

        '''
        phi = x_to_phi(x)
        if regression_type is GE:
             return mi_continuous(phi,y, knn)
        elif regression_type is NA:
            y (a matrix) -> y_list
            
            E.g. 
            x = [seq_1, seq_2]  
            y = [
                 [2,0],
                 [1,1]
                 ]         
            
            1. x_to_phi(x) -> phi=[phi_1, phi_2]
            2. phi->phi_list = [phi_1, phi_1, phi_2, phi_2]
            
            3. y -> y_list = [0, 0, 0, 1]
            
            return mi_mixed(phi_list,y_list, knn)
        '''
        if self.regression_type=='GE':
            return mi_continuous(self.x_to_phi(x), y, knn=5)

        elif self.regression_type=='NA':

            # TODO: needs to be implemented with correct y_format
            pass


    ## 20.08.24 CONTINUE HERE TOMORROW ##

    # def estimate_predictive_info(self,
    #                              sequences,
    #                              bin_counts):
    #
    #     """
    #     Method used to estimate the predictive information, or the
    #     mutual information I[y;yhat].
    #
    #     parameters
    #     ----------
    #
    #     sequences: (array-like of str)
    #         Sequence inputs representing DNA, RNA, or protein (whichever
    #         type of sequence the model was trained on). Input can must be
    #         an array of str, all the proper length.
    #
    #     bin_counts: (array-like)
    #         y represents counts in bins corresponding to the sequences X
    #
    #     returns
    #     -------
    #
    #     I_y_yhat: (float)
    #         Mutual information between y and y_hat
    #
    #     """
    #
    #     # compute the latent trait
    #     phi = self.x_to_phi(sequences)
    #
    #     p_of_b_given_phi = self.na_noisemodel(phi) # TODO: this is renamed na_p_of_ally_given_phi()
    #
    #     MI = 0
    #
    #     # M is total counts
    #     M = np.sum(bin_counts)
    #
    #     # This is p(b), but need to double check
    #     # i.e. fraciton of counts in bin i
    #     p_of_b = np.sum(bin_counts, axis=0) / M
    #
    #     for sequence_index in range(len(sequences)):
    #
    #         # from manuscript "Additionally, we approximate p(y| phi) by the inferred noise model pi of y given phi"
    #         # compute p_of_bin_given_phi
    #         p_of_b_given_phi_i = p_of_b_given_phi[sequence_index]
    #
    #
    #         # MI summand summed over b
    #         MI += np.sum(bin_counts[sequence_index] * np.log2((p_of_b_given_phi_i / p_of_b)))
    #     print(MI / M)

    def yhat_to_yq(self,
                   yhat,
                   q=[0.16,0.84]):
        """
        Method that returns the ge_noise_model, the ge probability distribution
        that models noise, from which the spatial parameter eta can be obtained.

        parameters
        ----------
        yhat: (array-like of floats)
            This is the values on which the spatial parameter will depend on.

        return

        mavenn._NoiseModel: (GE noise mdoel object)
            This can be Gaussian, Cauchy or SkewT.
        """

        check(self.regression_type=='GE', 'regression type must be GE for this methdd')
        # Get GE noise model based on the users input.
        return globals()[self.ge_noise_model_type + 'NoiseModel'](self,yhat,q=q).user_quantile_values


    def p_of_y_given_y_hat(self,
                           y,
                           yhat):

        """
        Method that returns computes.

        parameters
        ----------
        y: (array-like of floats)
            The y values for which the conditional probability will be computed.

        yhat: (float)
            The value on which the computed probability will be conditioned.

        returns
        -------
        p_of_y_given_yhat: (array-like of floats)
            Probability of y given sequence yhat. Shape of returned value will
            match shape of y_test, for a single yhat. For each value of yhat_i,
            the distribution p(y|yhat_i), where i traverses the elements of yhat.

        """

        if self.regression_type=='GE':
            # Get GE noise model based on the users input.
            ge_noise_model = globals()[self.ge_noise_model_type + 'NoiseModel'](self,yhat,None)

            return ge_noise_model.p_of_y_given_yhat(y, yhat)

        elif self.regression_type=='NA':
            # TODO: need to implement
            pass

    # TODO: this function is possibly experiencing a tensorflow backend bug with a single example x, need to check.
    def p_of_y_given_x(self, y, x):

        """
        Method that computes p_of_y_given_x.

        parameters
        ----------
        y: (array-like of floats)
            The y values for which the conditional probability will be computed.

        x: (array-like of strings)
            The value on which the computed probability will be conditioned.

        returns
        -------
        p_of_y_given_x: (array-like of floats)
            Probability of y given sequence x. Shape of returned value will
            match shape of y_test.

        """

        yhat = self.x_to_yhat(x)

        if self.regression_type=='GE':
            # Get GE noise model based on the users input.
            ge_noise_model = globals()[self.ge_noise_model_type + 'NoiseModel'](self,yhat)

            return ge_noise_model.p_of_y_given_yhat(y, yhat)

        elif self.regression_type=='NA':
            # TODO: need to implement
            pass


    def save(self,
             filename):

        """
        Method that will save the mave-nn model

        parameters
        ----------
        filename: (str)
            filename of the saved model.

        returns
        -------
        None

        """

        # save weights
        self.get_nn().save_weights(filename+'.h5')

        # save model configuration
        pd.DataFrame(self.__dict__).to_csv(filename+'.csv')