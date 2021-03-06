import numpy as np
import pandas as pd

from datetime import timedelta
import os
import json
import scipy

import readability
import re

from collections import defaultdict

pd.set_option('mode.chained_assignment', 'raise')

import custom_lda

_env = os.getenv("CACHEDIR")
if _env is not None:
    cache_dir = _env
else:
    cache_dir = "../cache/"

print("Cachedir gp_features>> {}".format(cache_dir))


raw_question_features_path = os.path.join(cache_dir, "ttm_elvis_raw_question_features.pickle")

class GP_Feature_Collection:

    def __init__(self, *args):

        self.features = args

    def update_event(self, event, user_answered, question_was_suggested):
        for f in self.features:
            f.update_event(event, user_answered, question_was_suggested)

    def update_pos_event(self, event):
        for f in self.features:
            f.update_event(event)

    def update_neg_event(self, event):
        for f in self.features:
            f.update_negative_event(event)

    def compute_features(self, user_id, questions, event_time=None):
        sub_features = [f.compute_features(user_id, questions, event_time) for f in self.features]

        all_features_mat = pd.concat(sub_features, axis=1) # np.concatenate(sub_features, axis = 1)

        return all_features_mat



class GP_Features:

    def update_generic_event(self, event, user_answered, question_was_suggested):
        """
        update a generic event

        :param event: pandas series
        :param user_answered: boolean wether the user answered or not (False if we know for sure that the user was suggested the question but didn't answer it)
        :param question_was_suggested: wheter the question was suggested or discovered, so far ignored.
        :return:
        """
        if user_answered:
            self.update_event(event)
        else:
            # we (assume to) know for sure that a user did not answer the question
            self.update_negative_event(event)

    def update_event(self, event) -> None:
        """ update interna"""
        pass

    def update_negative_event(self, event) -> None:
        """update with the event wehere we observe that a particular user DID NOT ANSWER a question ater it was suggested"""
        pass

    def compute_features(self, user_id, questions, event_time=None):
        # questions is a dataframe with one row per questions, and question features as columns
        #both same length for each a feature vector
        #return matrix of same length with a feature vector for the pair in each row
        pass


class GP_Features_user(GP_Features):
    """
    Implements features of both users, the answerer and the asker
    """
    def __init__(self, timedelta_wait=timedelta(days=2), feat_names=["num_posts", "num_answers", "num_questions", "answer_score", "question_score"]):
        self.user_features = defaultdict(list)
        self.timedelta_wait = timedelta_wait
        self.feat_names = feat_names

    def update_event(self, event):
        # [event.answer_score, event.answer_date] + curr_question_topics

        # for each user update a vector [num_posts, num_answers, num_questions, answer_score, question_score, acc_ans, has_acc_ans]  
        # TODO: add has_acc_ans and is_acc_ans
        # has_acc_ans =  1 if event.accepted_ans_id is not None  else 0
        # is_acc_ans = 1 if (event.accepted_ans_id is not None and event.accepted_ans_id==event.answer_id) else 0

        # answerer features
        answerer_feats = [event.answer_date, 1,1,0,event.answer_score, 0] # , is_acc_ans, 0]
        self.user_features[event.answerer_user_id].append(answerer_feats)
        # asker features: 
        # questions can appear several times, so first check if question in dict already
        ans_dates = [feat_vec[0] for feat_vec in self.user_features[event.asker_user_id]]
        same_date = [q_time==event.question_date for q_time in ans_dates]
        if not any(same_date):
            asker_feats = [event.question_date,1,0,1,0,event.question_score] # , 0, has_acc_ans]
            self.user_features[event.asker_user_id].append(asker_feats)

    def compute_features(self, user_id, questions, event_time=None):
        # filter the answerer features
        if user_id in self.user_features:
            user_feats_filter = self._filter_feats(self.user_features[user_id], event_time)
        else:
            user_feats_filter = [0 for _ in range(len(self.feat_names))]

        comb_feats = []
        for q_id, question in questions.iterrows():
            if question.question_owner_user_id in self.user_features: 
                question_feats_filter = self._filter_feats(self.user_features[question.question_owner_user_id], event_time)
            else:
                question_feats_filter = [0 for _ in range(len(self.feat_names))]
            # put answerer and asker features in one vector
            comb_feats.append(user_feats_filter+question_feats_filter) # pd.Series

        comb_cols = ["ans_"+col for col in self.feat_names] + ["ask_"+col for col in self.feat_names] 
        
        comb_df = pd.DataFrame(np.asarray(comb_feats), index=np.arange(len(questions)), columns=comb_cols)
        
        return comb_df

    def _filter_feats(self, user_feats, event_time=None):
        """
        helper function to return the accumulated features if the date is more than 2 days old
        @param user_feats: the raw feature vector [answer_date or question_date, num_posts, num_answers, ... etc]
        """
        user_feats_filter = []
        for v in user_feats:
            if event_time is not None and (event_time - v[0]) < self.timedelta_wait:
                user_feats_filter.append(v[1:4]+[0,0])
            else:
                user_feats_filter.append(v[1:])
        user_feats_filter = np.sum(np.asarray(user_feats_filter), axis=0)
        return user_feats_filter.tolist()


class GP_Features_affinity(GP_Features):
    """
    Implents user-question pair features based on tags
    """
    def __init__(self):
        self.user_tags = defaultdict(str)
        self.questions_asked = defaultdict(list)

    def update_event(self, event):
        # add tags to answerer
        self.user_tags[event.answerer_user_id] += event.question_tags # append to string
        # add tags to asker
        if event.question_id not in self.questions_asked[event.asker_user_id]: # if it is a new question asked by that user
            self.user_tags[event.asker_user_id] += event.question_tags
        # remember that this question was asked by the asker
        self.questions_asked[event.asker_user_id].append(event.question_id)

 
    def compute_features(self, user_id, questions, event_time=None):
        if user_id not in self.user_tags:
            return pd.DataFrame(0, index=np.arange(len(questions)), columns=["affinity_prod", "affinity_sum"])
        
        user_taglist = (self.user_tags[user_id])[1:-1].split("><") # list of tags
        unique_user_tags, counts = np.unique(user_taglist, return_counts=True)
        user_pdf = counts/np.sum(counts)
        affinity_list = []
        for q_id, question in questions.iterrows():
            question_tags = question.question_tags[1:-1].split("><")

            # topic affinity as in burel et al
            activated_tags = np.isin(unique_user_tags, question_tags)
            probs_of_activated = user_pdf[activated_tags]
            if len(probs_of_activated) > 0:
                prod = np.prod(probs_of_activated)
            else:
                prod = 0

            # TODO: reputation? more features based on tags?

            # sum of occurence counts of all tags
            occ_sum = sum([counts[unique_user_tags.tolist().index(t)] for t in question_tags if t in unique_user_tags])
            affinity_list.append([prod, occ_sum]) # pd.Series()

        # pairs_dataframe = pd.concat(affinity_list, axis=1).T
        pairs_dataframe = pd.DataFrame(np.asarray(affinity_list), index=np.arange(len(questions)), columns=["affinity_prod", "affinity_sum"])
        return pairs_dataframe


class GP_Features_Question(GP_Features):
    """
    Implements readability and thread features of the question
    """
    def __init__(self, timedelta_wait=timedelta(days=2)):
        self.timedelta_wait = timedelta_wait
        self.question_thread = defaultdict(list)
        self.all_tags = ''

    def update_event(self, event):
        # * readability features only need to be computed with the question body
        # * for thread features, add answer to the question with features
        # [num_answers, score_sum, is_accepted]
        
        if len(self.question_thread[event.question_id])==0:
            self.all_tags += event.question_tags # if question was not answered before, add tags to tag database
        # TODO: acc_ans
        self.question_thread[event.question_id].append([event.answer_date, 1, event.answer_score]) # 1 more answer, sum up the count

    def _cumulative_term_entropy(self, text):
        """
        Computes cumulative term entropy of a question body as specified in
        section 3.3.2 in the paper by Burel et.al.
        """
        for bad_words in [".", "<", ">", "/", "\n", "?p", "'", "(", ")"]:
            text = text.replace(bad_words, "")
        text_list = text.lower().split(" ")
        word_list, word_count = np.unique(text_list, return_counts=True)
        num_words = len(word_list)
        cte = word_count * (np.log(num_words) - np.log(word_count))/num_words
        return sum(cte)

    def compute_features(self, user_id, questions, event_time=None):
            # Remove html tags, numbers and code
        read_feats = questions[["question_id", "question_body"]].copy() # TODO the copy is just to not get the pandas settingWithCopy waranings all the time, might be slow?
        # Referral count
        read_feats.loc[:, "num_hyperlinks"] = read_feats['question_body'].str.count('href')
        # preprocess:
            #TODO c: moved the : to fron in the loc
        read_feats.loc[:, "question_body"] = read_feats["question_body"].str.replace(re.compile(r'<.*?>'), '')
        read_feats.loc[:, "question_body"] = read_feats["question_body"].str.replace(re.compile(r'(\d[\.]?)+'), '#N')
        read_feats.loc[:, "question_body"] = read_feats["question_body"].str.replace(re.compile(r'\$.*?\$'), '#M')

        read_feats.loc[:, "num_words"] = read_feats['question_body'].str.count(' ') + 1
        # GunningFogIndex and LIX
        readability_measures = read_feats["question_body"].apply(lambda x: readability.getmeasures(x, lang='en')['readability grades'])
        read_feats.loc[:, "GunningFogIndex"] = readability_measures.apply(lambda x: x['GunningFogIndex'])
        read_feats.loc[:, "LIX"] = readability_measures.apply(lambda x: x['LIX'])
        # Cumulative cross entropy
        read_feats.loc[:, "cumulative_term_entropy"] = read_feats["question_body"].apply(lambda x: self._cumulative_term_entropy(x))
        assert(len(read_feats)==len(questions))
        read_feats = read_feats.drop(["question_id", "question_body"], axis=1)

        # add thread features
        num_answers = []
        score_sum = []
        for q_id, question in questions.iterrows():
            thread = self._filter_feats(self.question_thread[question.question_id], event_time)
            num_answers.append(thread[0])
            score_sum.append(thread[1])

        question_feats = read_feats.set_index(np.arange(len(questions)))
        question_feats.loc[:, "num_ans_thread"] = pd.Series(num_answers)
        question_feats.loc[:, "scores_ans_thread"] = pd.Series(score_sum)

        # add tag popularity feature
        tag_popularity_sum = []
        for q_id, question in questions.iterrows():
            tag_list = question.question_tags[1:-1].split("><")
            occ_before = int(self.all_tags.count(question.question_tags) > 0) # question was answered before --> tags appear
            popularities = [self.all_tags.count("<"+tag+">") - occ_before for tag in tag_list] # need to add <> again because otherwise r will be found lots of times
            nr_all_tags = self.all_tags.count("><") + 1
            popularities = np.asarray(popularities)/float(nr_all_tags)
            tag_popularity_sum.append(sum(popularities))
        question_feats.loc[:, "tag_popularity"] = pd.Series(tag_popularity_sum)

        # add question age
        question_dates = [pd.Timestamp(x) for x in questions["question_date"].values]
        question_feats["question_age"] = [event_time - question_event_time for question_event_time in question_dates]
        question_feats["question_age"] = (question_feats["question_age"].dt.days +  (question_feats["question_age"].dt.seconds)/(24*60*60))
        
        return question_feats

    def _filter_feats(self, question_thread, event_time=None):
        """
        helper function to return the accumulated features if the date is more than 2 days old
        @param question_thread: the raw feature vector [timepoint, 1, score]
        """
        if len(question_thread)==0:
            return [0,0] # question has not been answered before

        # filter out the ones that are more less than two days old
        filtered_thread = []
        for q in question_thread:
            if event_time is not None and (event_time - q[0]) < self.timedelta_wait:
                filtered_thread.append([q[1], 0]) # set score to 0, delete first element since it is the timestamp
            else:
                filtered_thread.append(q[1:])
        filtered_thread = np.sum(np.asarray(filtered_thread), axis=0)
        return filtered_thread.tolist()


class GP_Features_TTM(GP_Features):

    def __init__(self, n_topics=10, timedelta_wait=timedelta(days=2), load_question_features=True):
        self.n_topics = n_topics
        self.timedelta_wait = timedelta_wait
        self.users_coupe_feats = dict()
        if load_question_features:
            self.ttm_questions_features = pd.read_pickle(raw_question_features_path)
        else:
            raise NotImplementedError("Need to copy-paste the code to compute TTM feats")
        self.ttm_questions_features.set_index('question_id', inplace=True)

    def update_event(self, event):
        ttm_question = self.ttm_questions_features.loc[event.question_id]
        question_topics = list(ttm_question[['topic_{}'.format(i) for i in range(self.n_topics)]])
        curr_question_topics = question_topics #TODO c: is this identity true?
        sing_feat_vector = [event.answer_score, event.answer_date] + curr_question_topics

        # TODO c: wrote self in front of all user_coupe_feats
        if event.answerer_user_id not in self.users_coupe_feats:
            self.users_coupe_feats[event.answerer_user_id] = [sing_feat_vector]
        else:
            self.users_coupe_feats[event.answerer_user_id].append(sing_feat_vector)

    def compute_features(self, user_id, questions, event_time=None):
        """
        Compute features for user-question pairs with a single user and a list of questions.

        :param user_id: A user id to find the "non-condensed" user (COUPE) feature list for the current user
        :param questions: Df of features for the selected questions
        :param event_time: If we specify an event time, we filter the user feat list based on this
        """

        q_u = {
            'votes_mean': 0, 'votes_sd': 0, 'votes_sum': 0, 'votes_max': 0, 'votes_min': 0, 'new': 1
        }

        feat_pairs = []
        for q_id, question in questions.iterrows():
            ttm_question = self.ttm_questions_features.loc[question.question_id]
            curr_question_topics = list(ttm_question[['topic_{}'.format(i) for i in range(self.n_topics)]])

            if user_id in self.users_coupe_feats:
                user_feats = self.users_coupe_feats[user_id]
                #If I have event_time, I filter first to check if I'm within "timedelta_wait" days
                if event_time is not None:
                    user_feats_filter = [v for v in user_feats if (event_time - v[1]) >= self.timedelta_wait]
                else:
                    user_feats_filter = user_feats

                if len(user_feats_filter) > 0 :
                    #I use v[0] as I assume that right now I only save votes and not the whole COUPE
                    #Otherwise use np.array(v[:4]) (the 4 depends on the no of coupe feats)
                    #Assume that the TTM vector in the user_feats starts from pos 2 (pos 1 is the date)
                    q_u_pre_agg = np.array([v[0] * (1 - scipy.spatial.distance.jensenshannon(curr_question_topics, v[2:])) for v in user_feats_filter])
                    q_u = {
                           'votes_mean': np.mean(q_u_pre_agg), 'votes_sd': np.std(q_u_pre_agg), 'votes_sum': np.sum(q_u_pre_agg), 'votes_max': np.max(q_u_pre_agg), 'votes_min': np.min(q_u_pre_agg), 'new': 0
                          }

            feat_pairs.append(pd.Series(q_u))
        pairs_dataframe = pd.concat(feat_pairs, axis=1).T
        return pairs_dataframe
